#!/usr/bin/env bash
# Schemathesis по OpenAPI — один прогін на операцію, кожен зі своїм акаунтом.
#
# ЧОМУ ОКРЕМИЙ АКАУНТ НА ОПЕРАЦІЮ, а не один на прогін. Дві причини, і жодна не
# косметична:
#
#  1. Три операції §9.2 руйнівні: `POST /v1/account/delete` стирає акаунт,
#     `POST /v1/consents/revoke` стирає сейф, `POST /v1/account/vault-reset` і
#     `POST /v1/sync/key {mode:"rekey"}` зсувають `reset_revision`, після чого
#     кожен наступний push відповідає 409 назавжди. З одним акаунтом їх
#     довелося б виключити, а виключити й не сказати — це тихе звуження
#     покриття. Тут вони фаззяться і руйнують лише власний акаунт.
#  2. Вікна §11 — per-account. З одним акаунтом бюджет `sync 60/хв` вичерпувався
#     на першій же операції, і решта прогону перевіряла виключно шлях 429:
#     361 запит, з них майже всі `rate_limited`. Ліміти при цьому НЕ
#     послаблюються — послабити їх заради зеленого фаззера було б рівно тим
#     дефектом, який ця фаза має ловити.
#
# ЩО РОБИТИ З 429. Навіть зі власним акаунтом операція вичерпує СВОЄ вікно за
# один прогін: pull генерує ~79 запитів на бюджет `sync 60/хв`. Два варіанти були
# хибні — послабити вікна §11 (це рівно той дефект, який фаза має ловити) і гнати
# фаззер повільніше за вікно (25 хвилин CI і жодної нової перевірки). Правильний
# лежить у `schemathesis.toml`: 429 додано до переліку статусів, які перевірки
# вважають правильною відповіддю, бо він нею і є — задокументована відмова з
# `Retry-After`, після якої запит не оброблявся. Ліміти не змінюються.
#
# ЯК МІНТИТЬСЯ СЕСІЯ: фікстурна Ed25519-пара з
# `apps/api/tests/integration/conftest.py` (насіння — байти 0..31), той самий
# публічний ключ, що вже стоїть у job `sync-e2e`. Секретом не є; приймає її лише
# api, налаштований саме на цей ключ.
#
# ВИКЛЮЧЕНИХ ПЕРЕВІРОК НЕМА. Працюють усі, включно з `not_a_server_error`: 500
# лишається дефектом і не має права стати очікуваним статусом. Уточнення
# переліків статусів для двох перевірок живе в `schemathesis.toml` разом із
# причиною.
#
# ВИКЛЮЧЕНИХ ОПЕРАЦІЙ ТЕЖ НЕМА — фаззяться всі п'ятнадцять. Єдине звуження:
# `POST /v1/sync/push` ганяється без coverage-фази, бо межа `maxItems: 200` разом
# із межею `payload_b64` дає тіло на ~17 МБ і понад дев'ять хвилин генерації.
# Причина, числа й чотири цільові тести, що стережуть ті самі межі, — у
# `schemathesis.toml`.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_URL="${API_URL:-http://localhost:8000}"
# Свідомо НЕ читає `TELEGRAM_BOT_ID` з оточення. Фікстурна пара §8 підписує
# `<bot_id>:WebAppData\n…`, тож id мусить збігатися з тим, на який налаштований
# api під тестом — а він налаштований на фікстурний. Успадкування довколишнього
# значення (наприклад із `.env`, де стоїть id реального бота) давало 401
# `auth_invalid`, що виглядає як дефект коду і ним не є.
BOT_ID="${FUZZ_BOT_ID:-1234567890}"
# Детермінований seed: без нього червоний CI неможливо відтворити.
SEED="${SCHEMATHESIS_SEED:-20260726}"
MAX_EXAMPLES="${SCHEMATHESIS_MAX_EXAMPLES:-20}"
# Перший id відліку. Кожна операція бере наступний: identity 1:1 з акаунтом
# (§4.3), тож інший id — це інший акаунт.
FIRST_USER_ID="${FUZZ_FIRST_USER_ID:-700000000}"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

curl -sf "$API_URL/health" >/dev/null || {
  echo "api не відповідає на $API_URL/health" >&2
  exit 1
}

# `mapfile` існує лише в bash 4+, а на macOS штатний bash — 3.2. Читання
# циклом працює в обох, і скрипт мусить бути запускним локально, не лише в CI.
OPERATIONS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && OPERATIONS+=("$line")
done < <(
  curl -sf "$API_URL/openapi.json" | python3 -c "
import json, sys
schema = json.load(sys.stdin)
for path, operations in sorted(schema['paths'].items()):
    for method, operation in sorted(operations.items()):
        # operationId, бо саме воно унікальне на операцію. '--include-path' разом
        # із '--include-method' дає ЇХ ОБ'ЄДНАННЯ, а не перетин: прогін для
        # POST /v1/account/delete вибирав усі вісім POST-ів і тривав десять
        # хвилин замість секунди. Знайдено на першому ж прогоні цього скрипта.
        print(method, path, operation['operationId'])
"
)

if [[ ${#OPERATIONS[@]} -eq 0 ]]; then
  echo "У схемі нуль операцій — фаззити нічого." >&2
  exit 1
fi

say "Операцій у схемі: ${#OPERATIONS[@]}; seed $SEED; прикладів на операцію $MAX_EXAMPLES"

failed=()
index=0

for operation in "${OPERATIONS[@]}"; do
  method="$(printf %s "$operation" | cut -d" " -f1)"
  path="$(printf %s "$operation" | cut -d" " -f2)"
  operation_id="$(printf %s "$operation" | cut -d" " -f3)"
  index=$((index + 1))
  user_id=$((FIRST_USER_ID + index))

  say "[$index/${#OPERATIONS[@]}] $(printf %s "$method" | tr a-z A-Z) $path"


  token="$(cd "$ROOT/apps/api" && uv run --locked python "$ROOT/scripts/mint_session.py" \
    --api-url "$API_URL" \
    --bot-id "$BOT_ID" \
    --telegram-user-id "$user_id" \
    --grant health_sync \
    --grant telegram_reminders)"

  # `--config-file` — опція групи, а не підкоманди `run`, тож стоїть ПЕРЕД `run`.
  # Автовиявлення знайшло б `schemathesis.toml` і саме (пошук угору від cwd), але
  # тоді прогін залежав би від того, звідки його запустили.
  if (cd "$ROOT/apps/api" && uv run --locked schemathesis \
    --config-file "$ROOT/schemathesis.toml" \
    run "$API_URL/openapi.json" \
    --url "$API_URL" \
    --header "Authorization: Bearer $token" \
    --include-operation-id "$operation_id" \
    --checks all \
    --seed "$SEED" \
    --max-examples "$MAX_EXAMPLES" \
    --no-color); then
    :
  else
    failed+=("$(printf %s "$method" | tr a-z A-Z) $path")
  fi
done

if [[ ${#failed[@]} -gt 0 ]]; then
  say "Провалено операцій: ${#failed[@]}"
  printf '  %s\n' "${failed[@]}"
  exit 1
fi

say "Усі ${#OPERATIONS[@]} операцій пройшли"
