#!/usr/bin/env bash
# Підіймає локальний стенд: PostgreSQL 16, міграції, паролі ролей, .env.
#
# Стенд свідомо працює в APP_ENV=development: тексти згод перебувають у версії
# 0.9 з плейсхолдером замість імені контролера, і в production-конфігурації
# сервер відмовляє у створенні згоди. Це не обхід — це той самий guard.
#
# BOT_TOKEN живе ЛИШЕ в apps/bot/.env (§5.3). У кореневий .env потрапляє тільки
# несекретний TELEGRAM_BOT_ID — числовий префікс токена.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BOT_ENV_FILE="$ROOT/apps/bot/.env"
PORT="${POSTGRES_PORT:-55432}"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }

random_secret() { LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 32; }

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Потрібен $1, але його немає в PATH." >&2
    exit 1
  }
}

require docker
require uv

# --- .env -------------------------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
  say "Створюю .env з випадковими паролями"
  cat >"$ENV_FILE" <<EOF
# Локальний стенд. Не для продакшену; у git не потрапляє.
POSTGRES_DB=neuro
POSTGRES_ADMIN_USER=neuro_admin
POSTGRES_ADMIN_PASSWORD=$(random_secret)
POSTGRES_PORT=$PORT

MIGRATION_DATABASE_URL=
API_DATABASE_URL=
REMINDER_WORKER_DATABASE_URL=

TELEGRAM_BOT_ID=
APP_ENV=development
WEBAPP_URL=
TELEGRAM_ED25519_PUBLIC_KEY=
LOG_ACCOUNT_REF_KEY=$(openssl rand -hex 32)
AUTH_MIN_AUTH_DATE=
AUTH_HMAC_FALLBACK_ENABLED=
WEBAPP_HMAC_SECRET=

# Паролі ролей застосунку — виставляються цим скриптом.
API_RW_PASSWORD=$(random_secret)
REMINDER_WORKER_PASSWORD=$(random_secret)
EOF
else
  say "Використовую наявний .env"
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# BOT_TOKEN не має потрапляти в оточення api: процес відмовиться стартувати.
unset BOT_TOKEN

# --- bot id з токена --------------------------------------------------------

if [[ -f "$BOT_ENV_FILE" ]]; then
  token="$(grep -E '^BOT_TOKEN=' "$BOT_ENV_FILE" | cut -d= -f2- | tr -d '"' || true)"
  if [[ -n "${token:-}" ]]; then
    bot_id="${token%%:*}"
    if [[ "$bot_id" =~ ^[0-9]+$ ]]; then
      # Тільки числовий префікс, ніколи сам токен.
      if grep -qE '^TELEGRAM_BOT_ID=' "$ENV_FILE"; then
        sed -i.bak "s/^TELEGRAM_BOT_ID=.*/TELEGRAM_BOT_ID=$bot_id/" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
      fi
      TELEGRAM_BOT_ID="$bot_id"
      export TELEGRAM_BOT_ID
      say "TELEGRAM_BOT_ID узято з apps/bot/.env: $bot_id"
    fi
  fi
fi

# Стенд має підійматися й до того, як з'явиться реальний бот: інакше Фазу 2
# неможливо розробляти. Фікстурне значення робить це явним, а не тихим.
FIXTURE_BOT_ID=1234567890
if [[ -z "${TELEGRAM_BOT_ID:-}" ]]; then
  sed -i.bak "s/^TELEGRAM_BOT_ID=.*/TELEGRAM_BOT_ID=$FIXTURE_BOT_ID/" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
  TELEGRAM_BOT_ID="$FIXTURE_BOT_ID"
  export TELEGRAM_BOT_ID
  USING_FIXTURE_BOT=1
fi

# WEBAPP_URL=https://… ./scripts/dev-stand.sh пропише URL в обидва .env одразу:
# боту він потрібен для кнопки, api — для CORS-allowlist, і вони мусять збігатися.
if [[ -n "${WEBAPP_URL:-}" ]]; then
  sed -i.bak "s|^WEBAPP_URL=.*|WEBAPP_URL=$WEBAPP_URL|" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
  if [[ -f "$BOT_ENV_FILE" ]]; then
    sed -i.bak "s|^WEBAPP_URL=.*|WEBAPP_URL=$WEBAPP_URL|" "$BOT_ENV_FILE"
    rm -f "$BOT_ENV_FILE.bak"
  fi
  say "WEBAPP_URL записано в обидва .env: $WEBAPP_URL"
  if [[ "$WEBAPP_URL" != https://* ]]; then
    warn "Telegram відкриває Mini App лише через HTTPS — цей URL не підійде."
  fi
elif [[ -z "$(grep -E '^WEBAPP_URL=' "$ENV_FILE" | cut -d= -f2-)" ]]; then
  sed -i.bak "s|^WEBAPP_URL=.*|WEBAPP_URL=http://localhost:5173|" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
  WEBAPP_URL="http://localhost:5173"
  export WEBAPP_URL
fi

# --- PostgreSQL -------------------------------------------------------------

say "Підіймаю PostgreSQL 16"
docker compose --env-file "$ENV_FILE" up -d postgres >/dev/null

printf 'Чекаю на готовність'
until docker compose --env-file "$ENV_FILE" exec -T postgres \
  pg_isready -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
  printf '.'
  sleep 1
done
printf ' готово\n'

ADMIN_URL="postgresql://$POSTGRES_ADMIN_USER:$POSTGRES_ADMIN_PASSWORD@127.0.0.1:$POSTGRES_PORT/$POSTGRES_DB"

say "Застосовую міграції"
(cd "$ROOT/apps/api" && MIGRATION_DATABASE_URL="$ADMIN_URL" \
  uv run --locked alembic upgrade head 2>&1 | grep -E 'Running upgrade|already at' || true)

say "Виставляю паролі ролей застосунку"
# Міграція створює ролі без паролів — інакше секрет опинився б у git.
docker compose --env-file "$ENV_FILE" exec -T postgres psql -q \
  -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" >/dev/null <<SQL
ALTER ROLE api_rw PASSWORD '$API_RW_PASSWORD';
ALTER ROLE reminder_worker PASSWORD '$REMINDER_WORKER_PASSWORD';
SQL

python3 - "$ENV_FILE" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
values = dict(
    line.split("=", 1)
    for line in text.splitlines()
    if line and not line.startswith("#") and "=" in line
)
host = f"127.0.0.1:{values['POSTGRES_PORT']}/{values['POSTGRES_DB']}"
urls = {
    "MIGRATION_DATABASE_URL":
        f"postgresql://{values['POSTGRES_ADMIN_USER']}:{values['POSTGRES_ADMIN_PASSWORD']}@{host}",
    "API_DATABASE_URL": f"postgresql://api_rw:{values['API_RW_PASSWORD']}@{host}",
    "REMINDER_WORKER_DATABASE_URL":
        f"postgresql://reminder_worker:{values['REMINDER_WORKER_PASSWORD']}@{host}",
}
for key, url in urls.items():
    text = re.sub(rf"^{key}=.*$", f"{key}={url}", text, flags=re.M)
open(path, "w", encoding="utf-8").write(text)
PY

say "Стенд готовий"
cat <<'EOF'

  Запустити api:
    set -a; . .env; set +a; unset BOT_TOKEN
    cd apps/api && uv run --locked uvicorn app.main:app_factory --factory --reload --no-access-log

  Перевірити:
    ./scripts/dev-smoke.sh

  Зупинити:
    docker compose --env-file .env down

EOF

if [[ "${USING_FIXTURE_BOT:-0}" == "1" ]]; then
  warn "TELEGRAM_BOT_ID фікстурний ($FIXTURE_BOT_ID): initData від справжнього"
  warn "Telegram не пройде перевірку підпису. Створи apps/bot/.env з BOT_TOKEN"
  warn "і перезапусти цей скрипт, коли бот з'явиться."
  printf '\n'
fi

warn "Тексти згод — версія 0.9, контролер не названий."
warn "Стенд працює в APP_ENV=development, і лише тому приймає згоди."
warn "У production-конфігурації той самий код віддає 503 consent_copy_not_frozen."
