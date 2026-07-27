#!/usr/bin/env bash
# Підіймає весь стенд однією командою: тунель, PostgreSQL, міграції, web, api, бот.
#
# Тунель ngrok скрипт піднімає сам і гасить разом з усім іншим. Раніше він лише
# зазирав у локальний API ngrok на :4040 і, не знайшовши агента, мовчки брав
# WEBAPP_URL зі старого .env — див. розділ «тунель», це коштувало окремого
# класу помилок. Ctrl+C гасить усе, включно з тунелем.
#
# BOT_TOKEN лишається виключно в apps/bot/.env: кожен процес запускається у
# власному підшелі, і оточення api його не бачить (§5.3).
#
# За замовчуванням підіймається ПОВНИЙ стенд: web зі мережевим кодом, екраном
# нагадувань і api за тим самим тунелем. Тобто ця команда достатня, щоб пройти
# шлях Mini App → api → БД із телефона:
#
#   ./scripts/dev-start.sh --set-menu-button
#
# api не потребує власного публічного домену: dev-сервер проксує `/v1/...` і
# `/health` на localhost:API_PORT, тож web і api — одне походження. Це знімає
# і вимогу другого тунелю (акаунт із одним доменом його не дасть), і CORS.
#
# Звузити стенд можна явно:
#
#   SYNC=off ./scripts/dev-start.sh        local-only: нуль мережевих викликів,
#                                          рівно так застосунок працює для тих,
#                                          хто синхронізацію не вмикав
#   REMINDERS=off ./scripts/dev-start.sh   форма, якою пішов би продакшен:
#                                          sync є, екрана нагадувань немає
#
# Обидва прапорці статичні й діють на збірку, а не на рантайм: за `off`
# відповідного коду в бандлі немає фізично (перевіряє scripts/assert-bundle.mjs).
#
# Прапорці:
#   --set-menu-button   виставити кнопку меню, навіть якщо її ще немає
#   --no-menu-button    не чіпати кнопку меню взагалі
#
# Змінні оточення: API_PORT, WEB_PORT, SYNC, REMINDERS, NGROK_BIN, NGROK_DOMAIN.

set -euo pipefail

# Керування завданнями увімкнено навмисно: без нього кожен фоновий блок лишається
# в групі процесів самого скрипта, і `kill` по його PID гасить лише обгортку —
# `uv`, `uvicorn`, `vite` і бот під нею переживають зупинку й далі тримають порти.
# Наступний запуск падає на «Порт 5173 зайнятий», причина якого була хвилиною
# раніше й виглядала як успішне «Зупинено.». З `set -m` кожен блок дістає власну
# групу, і cleanup гасить її цілком.
set -m

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BOT_ENV_FILE="$ROOT/apps/bot/.env"
LOG_DIR="$ROOT/.dev-logs"
NGROK_LOG="$LOG_DIR/ngrok.log"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
SYNC="${SYNC:-on}"
REMINDERS="${REMINDERS:-on}"
NGROK_BIN="${NGROK_BIN:-ngrok}"
NGROK_DOMAIN="${NGROK_DOMAIN:-}"
SET_MENU_BUTTON=0
TOUCH_MENU_BUTTON=1

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok() { printf '\033[32m  ok\033[0m  %s\n' "$1"; }
warn() { printf '\033[33m  ⚠ \033[0m %s\n' "$1"; }
die() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --set-menu-button) SET_MENU_BUTTON=1 ;;
    --no-menu-button) TOUCH_MENU_BUTTON=0 ;;
    *) die "Невідомий аргумент: $1" ;;
  esac
  shift
done

mkdir -p "$LOG_DIR"
: >"$NGROK_LOG"

# --- зупинка ----------------------------------------------------------------
#
# Оголошено до першого запущеного процесу навмисно: тунель ми піднімаємо самі й
# раніше за все інше, тож без пастки тут ngrok пережив би Ctrl+C і лишився б
# висіти. Наступний запуск побачив би чужого агента й повівся б інакше, ніж
# розробник очікує, — а причина була б за годину до того.

PIDS=()
STARTED=0
STAND_UP=0

cleanup() {
  # Пастка знімається одразу: інакше після TERM вона відпрацює вдруге на EXIT —
  # два «Зупинено.» і два `docker compose down`.
  trap - EXIT INT TERM
  if [[ "$STARTED" == "0" && "$STAND_UP" == "0" ]]; then
    return 0
  fi
  printf '\n\033[1mЗупиняю\033[0m\n'
  # Спершу вся група (див. `set -m` вище), і лише як запасний варіант — сам PID.
  for pid in "${PIDS[@]:-}"; do
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  if [[ "$STAND_UP" == "1" ]]; then
    (cd "$ROOT" && docker compose --env-file "$ENV_FILE" down >/dev/null 2>&1) || true
  fi
  printf 'Зупинено.\n'
}
trap cleanup EXIT INT TERM

# --- порти ------------------------------------------------------------------
#
# Перевірка тут, а не «якось потім», через конкретний випадок, що коштував
# години: vite за зайнятого порту мовчки бере наступний, а тунель лишається
# націленим на початковий. Стенд рапортує «готово», Mini App відкривається — і
# віддає його **чужий** процес, який може бути навіть із учорашньої сесії.
# Симптом при цьому виглядає як «api не отримує запитів», тобто дивишся зовсім
# не туди. Краще не стартувати, ніж стартувати не тим.

port_owner() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -Fpc 2>/dev/null |
    awk '/^p/{pid=substr($0,2)} /^c/{print pid" ("substr($0,2)")"; exit}'
}

say "Перевіряю порти"
for spec in "web:$WEB_PORT" "api:$API_PORT"; do
  name="${spec%%:*}"
  port="${spec##*:}"
  owner="$(port_owner "$port" || true)"
  [[ -z "$owner" ]] ||
    die "Порт $port ($name) зайнятий: $owner
Це майже завжди процес попереднього стенду. Зупини його й спробуй ще раз:
  kill ${owner%% *}"
done
ok "порти $WEB_PORT і $API_PORT вільні"

# --- тунель -----------------------------------------------------------------
#
# Той самий принцип, що й з портами, і той самий клас помилки. Раніше цей блок
# робив один запит до :4040 і, не діставши відповіді, брав WEBAPP_URL зі старого
# .env — адресу вчорашнього тунелю. Далі стенд рапортував «Готово» і показував
# цю адресу як робочу, а Telegram, відкриваючи кнопку, отримував від ngrok 404 з
# `ngrok-error-code: ERR_NGROK_3200` (endpoint offline). З --set-menu-button та
# сама мертва адреса ще й записувалась у Telegram і переживала перезапуск.
#
# Тому фолбеку більше немає: або тунель є, або стенду немає.

# Поле тунелю, що обслуговує вказаний порт: `public_url` — адреса, `name` — ім'я
# в агента (щоб зняти саме той тунель, який завели ми, а не чужий).
# Точний збіг за портом, а не підрядок: '5173' знаходився б і в '51730'.
tunnel_field() {
  curl -s --max-time 5 http://127.0.0.1:4040/api/tunnels 2>/dev/null |
    python3 -c "
import json, sys
want, field = ':' + sys.argv[1], sys.argv[2]
try:
    tunnels = json.load(sys.stdin).get('tunnels', [])
except Exception:
    sys.exit()
for t in tunnels:
    if t.get('proto') == 'https' and t.get('config', {}).get('addr', '').endswith(want):
        print(t.get(field, ''))
        break
" "$1" "$2" || true
}

find_tunnel() { tunnel_field "$1" public_url; }

agent_alive() { curl -s --max-time 2 -o /dev/null http://127.0.0.1:4040/api/tunnels; }

# Результат — у глобальній TUNNEL_URL, а не в stdout, і це не стиль, а вимога:
# `$( )` виконався б у підшелі, тож $! запущеного ngrok не потрапив би до PIDS
# і тунель пережив би Ctrl+C.
TUNNEL_URL=""
ensure_tunnel() {
  local port="$1" label="$2" ngrok_pid=""
  local -a args

  TUNNEL_URL="$(find_tunnel "$port")"
  # Чужого агента поважаємо: ngrok у сусідньому вікні — теж робочий тунель.
  [[ -z "$TUNNEL_URL" ]] || return 0

  if agent_alive; then
    # Агент є, тунелю на цей порт немає — просимо його ж додати ще один.
    curl -s --max-time 5 -X POST http://127.0.0.1:4040/api/tunnels \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"$label-$port\",\"proto\":\"http\",\"addr\":\"$port\"}" \
      >>"$NGROK_LOG" 2>&1 || true
    printf '\n' >>"$NGROK_LOG"
  else
    command -v "$NGROK_BIN" >/dev/null 2>&1 ||
      die "Не знайдено ngrok ($NGROK_BIN). Постав його або вкажи шлях:
  NGROK_BIN=/шлях/до/ngrok ./scripts/dev-start.sh"
    args=(http "$port" --log=stdout --log-format=logfmt)
    [[ -z "$NGROK_DOMAIN" ]] || args+=(--url="$NGROK_DOMAIN")
    "$NGROK_BIN" "${args[@]}" >>"$NGROK_LOG" 2>&1 &
    ngrok_pid=$!
    PIDS+=("$ngrok_pid")
    STARTED=1
  fi

  for _ in $(seq 1 20); do
    TUNNEL_URL="$(find_tunnel "$port")"
    [[ -z "$TUNNEL_URL" ]] || return 0
    # Впав одразу (немає токена, вичерпано ліміт агентів) — не чекаємо намарно.
    if [[ -n "$ngrok_pid" ]] && ! kill -0 "$ngrok_pid" 2>/dev/null; then
      return 1
    fi
    sleep 1
  done
  return 1
}

tunnel_failure() {
  local tail_lines
  tail_lines="$(tail -5 "$NGROK_LOG" 2>/dev/null)"
  printf 'Тунель на порт %s не піднявся.\n' "$1"
  # Порожній лог теж сам собою відповідь: ngrok не сказав узагалі нічого.
  if [[ -n "$tail_lines" ]]; then
    printf '\nОстанні рядки %s:\n%s\n' "$NGROK_LOG" "$tail_lines"
  else
    printf '%s не містить нічого — ngrok навіть не почав.\n' "$NGROK_LOG"
  fi
}

say "Підіймаю тунель"
ensure_tunnel "$WEB_PORT" web ||
  die "$(tunnel_failure "$WEB_PORT")
Найчастіші причини: не заданий authtoken (ngrok config add-authtoken …),
уже працює інший агент ngrok, або вичерпано ліміт тарифу."
WEB_TUNNEL="$TUNNEL_URL"
ok "ngrok: $WEB_TUNNEL"

# Обидва .env мусять нести один і той самий URL: бот — для кнопки, api — для CORS.
for file in "$ENV_FILE" "$BOT_ENV_FILE"; do
  [[ -f "$file" ]] || continue
  if grep -qE '^WEBAPP_URL=' "$file"; then
    sed -i.bak "s|^WEBAPP_URL=.*|WEBAPP_URL=$WEB_TUNNEL|" "$file"
    rm -f "$file.bak"
  else
    # У файлі без такого рядка sed не замінює нічого й мовчить, тож бот стартував
    # би без WEBAPP_URL і впав на «Не задано в env» — за крок від справжньої причини.
    printf 'WEBAPP_URL=%s\n' "$WEB_TUNNEL" >>"$file"
  fi
done
ok "WEBAPP_URL записано в обидва .env"

# --- походження api для web -------------------------------------------------
#
# api їде ТИМ САМИМ тунелем, що й web, через проксі dev-сервера
# (`server.proxy` у apps/web/vite.config.ts). Причина не в зручності:
# Telegram відкриває Mini App лише через HTTPS, а акаунт ngrok з одним доменом
# не дає api власної публічної адреси — другий тунель отримує ту саму. Раніше
# скрипт це розпізнавав і вироджувався в `http://localhost:8000`, до якого
# телефон не достукається, тож шлях Mini App → api → БД неможливо було пройти
# руками взагалі.
#
# Отже `VITE_API_ORIGIN` лишається порожнім свідомо: клієнт ходить відносними
# шляхами `/v1/...` на власне походження, `connect-src 'self'` їх покриває, а
# dev-сервер передає їх на api. CORS у цій схемі не бере участі — і це названо
# як втрату fidelity у vite.config.ts: крос-origin перевіряє job `sync-e2e`.

API_ORIGIN=""
API_PROXY=""
if [[ "$SYNC" == "on" ]]; then
  API_PROXY="http://localhost:$API_PORT"
  ok "api через тунель web: $WEB_TUNNEL/v1/... → $API_PROXY"
fi

# --- база й міграції --------------------------------------------------------

say "База, міграції, ролі"
WEBAPP_URL="$WEB_TUNNEL" "$ROOT/scripts/dev-stand.sh" >"$LOG_DIR/stand.log" 2>&1 ||
  { cat "$LOG_DIR/stand.log"; die "dev-stand.sh не відпрацював"; }
STAND_UP=1
ok "PostgreSQL піднято, міграції застосовано"
grep -E 'TELEGRAM_BOT_ID узято' "$LOG_DIR/stand.log" >/dev/null &&
  ok "TELEGRAM_BOT_ID: $(grep -E '^TELEGRAM_BOT_ID=' "$ENV_FILE" | cut -d= -f2-)"

# --- процеси ----------------------------------------------------------------

say "Стартую процеси"

# web — `--strictPort`, бо тихий відкат на сусідній порт лишає тунель націленим
# на чужий процес (див. «Перевіряю порти»).
(
  cd "$ROOT/apps/web"
  export VITE_SYNC="$SYNC"
  export VITE_REMINDERS="$REMINDERS"
  export VITE_API_ORIGIN="$API_ORIGIN"
  export VITE_API_PROXY="$API_PROXY"
  pnpm dev --port "$WEB_PORT" --strictPort >"$LOG_DIR/web.log" 2>&1
) &
PIDS+=($!)
STARTED=1

# api — власний підшел, BOT_TOKEN знято явно
(
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  unset BOT_TOKEN
  cd "$ROOT/apps/api"
  uv run --locked uvicorn app.main:app_factory --factory \
    --port "$API_PORT" --reload --no-access-log >"$LOG_DIR/api.log" 2>&1
) &
PIDS+=($!)

# бот — власний підшел, читає лише apps/bot/.env
(
  set -a
  # shellcheck disable=SC1090
  . "$BOT_ENV_FILE"
  set +a
  cd "$ROOT/apps/bot"
  uv run --locked python -m bot.main >"$LOG_DIR/bot.log" 2>&1
) &
PIDS+=($!)

wait_for() {
  local name="$1" url="$2"
  for _ in $(seq 1 40); do
    curl -sf "$url" >/dev/null 2>&1 && { ok "$name"; return 0; }
    sleep 1
  done
  warn "$name не піднявся — дивись $LOG_DIR"
  return 1
}

wait_for "web  → http://localhost:$WEB_PORT" "http://localhost:$WEB_PORT" || true
wait_for "api  → http://localhost:$API_PORT/health" "http://localhost:$API_PORT/health" || true

sleep 2
if grep -qiE "unauthorized|token is invalid|Не задано в env" "$LOG_DIR/bot.log" 2>/dev/null; then
  warn "бот: токен не прийнято — дивись $LOG_DIR/bot.log"
elif grep -qi "polling" "$LOG_DIR/bot.log" 2>/dev/null; then
  ok "бот  → polling"
else
  ok "бот  → запущено"
fi

# --- тунель справді віддає застосунок ---------------------------------------
#
# Локальний /health не каже нічого про те, що бачить Telegram: між ними тунель.
# Тому останній бар'єр — запит **публічною адресою**, і саме тією, яку зараз
# отримає кнопка. Перевірка стоїть після старту vite (живий тунель до порту, що
# ще не слухає, дав би хибну помилку) і до запису адреси в кнопку меню.

probe_tunnel() {
  local url="$1" headers status code
  headers="$(mktemp)"
  status="$(curl -s -o /dev/null -D "$headers" -w '%{http_code}' --max-time 15 \
    -H 'ngrok-skip-browser-warning: 1' "$url" 2>/dev/null || true)"
  code="$(awk -F': ' 'tolower($1)=="ngrok-error-code"{gsub(/\r/,"",$2); print $2; exit}' \
    "$headers" 2>/dev/null)"
  rm -f "$headers"
  printf '%s %s' "${status:-000}" "${code:-none}"
}

say "Перевіряю тунель"
read -r PROBE_STATUS PROBE_CODE <<<"$(probe_tunnel "$WEB_TUNNEL")"
case "$PROBE_CODE" in
  none)
    if [[ "$PROBE_STATUS" == "000" ]]; then
      die "Тунель $WEB_TUNNEL не відповідає взагалі (мережа? DNS?).
Кнопка в Telegram віддала б помилку, тож стенд не віддаю як робочий."
    fi
    ok "тунель віддає застосунок (HTTP $PROBE_STATUS)"
    ;;
  ERR_NGROK_32*)
    die "Тунель $WEB_TUNNEL мертвий: $PROBE_CODE (HTTP $PROBE_STATUS).
Саме цю сторінку Telegram показує замість Mini App. Дивись $NGROK_LOG."
    ;;
  ERR_NGROK_80*)
    warn "тунель живий, але web за ним не відповідає: $PROBE_CODE"
    warn "дивись $LOG_DIR/web.log"
    ;;
  *)
    warn "ngrok відповів $PROBE_CODE (HTTP $PROBE_STATUS) — дивись $NGROK_LOG"
    ;;
esac

# --- кнопка меню ------------------------------------------------------------
#
# Кнопка меню живе на боці Telegram і про наш тунель нічого не знає: раз
# записаний URL лишається в ній назавжди. Поки ngrok віддає ту саму адресу, це
# непомітно, але щойно вона зміниться — кожна сесія починатиметься з
# ERR_NGROK_3200 ще до того, як щось запрацює, і жоден лог стенду цього не
# покаже. Тому кнопку, яка вже є, тримаємо в актуальному стані; кнопку, якої
# немає, самі не заводимо — для цього є --set-menu-button.
#
# Токен не потрапляє ні в аргументи (їх видно в `ps` будь-кому в системі), ні у
# вивід: curl бере адресу зі stdin через --config.

tg() {
  local method="$1"
  shift
  curl -s --max-time 10 "$@" --config - <<<"url = \"https://api.telegram.org/bot$TOKEN/$method\""
}

# Друкує «<тип> <url>». Тип `error` означає «Telegram не відповів або відмовив» —
# це не те саме, що «кнопки немає», і поводитись з ним треба інакше.
menu_button_state() {
  tg getChatMenuButton | python3 -c "
import json, sys
try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}
if not payload.get('ok'):
    print('error', '')
    sys.exit()
result = payload.get('result') or {}
url = (result.get('web_app') or {}).get('url', '')
print(result.get('type', 'none'), url.rstrip('/'))
"
}

set_menu_button() {
  tg setChatMenuButton -H 'Content-Type: application/json' \
    -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Щоденник\",\"web_app\":{\"url\":\"$WEB_TUNNEL\"}}}"
}

if [[ "$TOUCH_MENU_BUTTON" == "1" ]]; then
  say "Кнопка меню бота"
  TOKEN="$(grep -E '^BOT_TOKEN=' "$BOT_ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
  if [[ -z "$TOKEN" ]]; then
    warn "BOT_TOKEN не знайдено в $BOT_ENV_FILE — кнопку не чіпаю"
  else
    read -r MENU_TYPE MENU_URL <<<"$(menu_button_state)"
    if [[ "$MENU_TYPE" == "error" ]]; then
      warn "Telegram не сказав, яка зараз кнопка меню — лишаю як є"
    elif [[ "$MENU_TYPE" == "web_app" && "$MENU_URL" == "${WEB_TUNNEL%/}" ]]; then
      ok "кнопка меню вже веде на $WEB_TUNNEL"
    elif [[ "$MENU_TYPE" == "web_app" || "$SET_MENU_BUTTON" == "1" ]]; then
      RESULT="$(set_menu_button)"
      if grep -q '"ok":true' <<<"$RESULT"; then
        if [[ "$MENU_TYPE" == "web_app" ]]; then
          ok "кнопку меню оновлено: $MENU_URL → $WEB_TUNNEL"
        else
          ok "кнопку «Щоденник» встановлено на $WEB_TUNNEL"
        fi
      else
        warn "Telegram відмовив: $(sed 's/.*"description":"\([^"]*\)".*/\1/' <<<"$RESULT")"
      fi
    else
      ok "кнопки меню немає — не заводжу її сам"
      warn "щоб з'явилася: ./scripts/dev-start.sh --set-menu-button"
    fi
    unset TOKEN
  fi
fi

# --- підсумок ---------------------------------------------------------------

say "Готово"
cat <<EOF

  Mini App:   $WEB_TUNNEL
  api:        http://localhost:$API_PORT/health
  режим web:  $([[ "$SYNC" == "on" ]] && printf 'sync → %s (той самий тунель)' "$WEB_TUNNEL" || printf 'local-only')
  нагадування:$([[ "$SYNC" == "on" && "$REMINDERS" == "on" ]] && printf ' увімкнені' || printf ' недоступні')
  логи:       $LOG_DIR/{ngrok,web,api,bot}.log

EOF

if [[ "$SYNC" == "on" ]]; then
  ok "Mini App автентифікується в api через той самий тунель — CORS не задіяний."
else
  warn "web у local-only режимі — мережевих викликів немає, api й бот під час"
  warn "користування не задіяні. Для повного шляху досить запустити без SYNC=off."
fi
warn "Тексти згод — 0.9, контролер не названий; стенд приймає згоди лише в development."
if [[ "$REMINDERS" == "on" && "$SYNC" != "on" ]]; then
  warn "REMINDERS=on без SYNC=on нічого не вмикає: розклад ходить сесією синхронізації."
elif [[ "$REMINDERS" == "on" ]]; then
  warn "Нагадування ввімкнені. У продакшеновій збірці їх немає: gate"
  warn "future-telegram-reminders.md відкритий (review доставки не підписаний)."
else
  ok "Нагадування вимкнені — саме та форма збірки, якою пішов би продакшен."
fi

printf '\nCtrl+C зупиняє все.\n'
wait
