#!/usr/bin/env bash
# Підіймає весь стенд однією командою: PostgreSQL, міграції, web, api, бот.
#
# URL тунелю береться з ngrok автоматично (його локальний API на :4040), тож
# копіювати адресу руками не треба. Ctrl+C гасить усе.
#
# BOT_TOKEN лишається виключно в apps/bot/.env: кожен процес запускається у
# власному підшелі, і оточення api його не бачить (§5.3).
#
# За замовчуванням web підіймається в local-only режимі: він не робить жодного
# мережевого виклику, і це навмисно — саме так застосунок працює для тих, хто
# синхронізацію не вмикав. Щоб пройти шлях Mini App → api → БД, потрібен
# SYNC=on (див. нижче): без нього бандл фізично не містить мережевого коду,
# тож ані `/v1/auth/telegram`, ані згоди, ані нагадувань із телефона не буде.
#
#   SYNC=on ./scripts/dev-start.sh
#
# Телефон ходить до api з інтернету, тому за SYNC=on потрібен тунель і на
# api-порт, а не лише на web. Скрипт шукає його там само і, якщо не знайде,
# каже про це прямо замість того, щоб віддати стенд, у якому Mini App
# мовчки не достукається.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BOT_ENV_FILE="$ROOT/apps/bot/.env"
LOG_DIR="$ROOT/.dev-logs"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
SYNC="${SYNC:-off}"
SET_MENU_BUTTON=0

[[ "${1:-}" == "--set-menu-button" ]] && SET_MENU_BUTTON=1

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok() { printf '\033[32m  ok\033[0m  %s\n' "$1"; }
warn() { printf '\033[33m  ⚠ \033[0m %s\n' "$1"; }
die() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

mkdir -p "$LOG_DIR"

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

# Точний збіг за портом, а не підрядок: '5173' знаходився б і в '51730'.
find_tunnel() {
  curl -s --max-time 5 http://127.0.0.1:4040/api/tunnels 2>/dev/null |
    python3 -c "
import json, sys
want = ':' + sys.argv[1]
try:
    tunnels = json.load(sys.stdin).get('tunnels', [])
except Exception:
    sys.exit()
for t in tunnels:
    if t.get('proto') == 'https' and t.get('config', {}).get('addr', '').endswith(want):
        print(t['public_url'])
        break
" "$1" || true
}

say "Шукаю тунель"
TUNNEL_URL="$(find_tunnel "$WEB_PORT")"

if [[ -n "$TUNNEL_URL" ]]; then
  ok "ngrok: $TUNNEL_URL"
else
  TUNNEL_URL="$(grep -E '^WEBAPP_URL=' "$ENV_FILE" | cut -d= -f2-)"
  [[ "$TUNNEL_URL" == https://* ]] ||
    die "Тунеля немає, а WEBAPP_URL у .env не HTTPS ($TUNNEL_URL).
Запусти в іншому вікні:  ngrok http $WEB_PORT"
  warn "ngrok API недоступний — беру WEBAPP_URL з .env: $TUNNEL_URL"
fi

# Обидва .env мусять нести один і той самий URL: бот — для кнопки, api — для CORS.
for file in "$ENV_FILE" "$BOT_ENV_FILE"; do
  [[ -f "$file" ]] || continue
  sed -i.bak "s|^WEBAPP_URL=.*|WEBAPP_URL=$TUNNEL_URL|" "$file"
  rm -f "$file.bak"
done
ok "WEBAPP_URL записано в обидва .env"

# --- походження api для web -------------------------------------------------

API_ORIGIN=""
if [[ "$SYNC" == "on" ]]; then
  API_ORIGIN="$(find_tunnel "$API_PORT")"
  if [[ -n "$API_ORIGIN" ]]; then
    ok "api-тунель: $API_ORIGIN"
  else
    API_ORIGIN="http://localhost:$API_PORT"
    warn "тунелю на api-порт немає — беру $API_ORIGIN"
    warn "у браузері на цій машині це працює; з телефона Telegram до localhost"
    warn "не достукається, тож Mini App покаже помилку мережі. Щоб було чесно:"
    warn "  ngrok http $API_PORT   (в окремому вікні, поруч із тунелем на web)"
  fi
fi

# --- база й міграції --------------------------------------------------------

say "База, міграції, ролі"
WEBAPP_URL="$TUNNEL_URL" "$ROOT/scripts/dev-stand.sh" >"$LOG_DIR/stand.log" 2>&1 ||
  { cat "$LOG_DIR/stand.log"; die "dev-stand.sh не відпрацював"; }
ok "PostgreSQL піднято, міграції застосовано"
grep -E 'TELEGRAM_BOT_ID узято' "$LOG_DIR/stand.log" >/dev/null &&
  ok "TELEGRAM_BOT_ID: $(grep -E '^TELEGRAM_BOT_ID=' "$ENV_FILE" | cut -d= -f2-)"

# --- процеси ----------------------------------------------------------------

PIDS=()
cleanup() {
  printf '\n\033[1mЗупиняю\033[0m\n'
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" down >/dev/null 2>&1) || true
  printf 'Зупинено.\n'
}
trap cleanup EXIT INT TERM

say "Стартую процеси"

# web — `--strictPort`, бо тихий відкат на сусідній порт лишає тунель націленим
# на чужий процес (див. «Перевіряю порти»).
(
  cd "$ROOT/apps/web"
  export VITE_SYNC="$SYNC"
  export VITE_API_ORIGIN="$API_ORIGIN"
  pnpm dev --port "$WEB_PORT" --strictPort >"$LOG_DIR/web.log" 2>&1
) &
PIDS+=($!)

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

# --- кнопка меню (лише за явним проханням) ----------------------------------

if [[ "$SET_MENU_BUTTON" == "1" ]]; then
  say "Ставлю кнопку меню бота"
  TOKEN="$(grep -E '^BOT_TOKEN=' "$BOT_ENV_FILE" | cut -d= -f2-)"
  RESULT="$(curl -s "https://api.telegram.org/bot$TOKEN/setChatMenuButton" \
    -H 'Content-Type: application/json' \
    -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Щоденник\",\"web_app\":{\"url\":\"$TUNNEL_URL\"}}}")"
  if grep -q '"ok":true' <<<"$RESULT"; then
    ok "кнопку «Щоденник» встановлено на $TUNNEL_URL"
  else
    warn "Telegram відмовив: $(sed 's/.*"description":"\([^"]*\)".*/\1/' <<<"$RESULT")"
  fi
fi

# --- підсумок ---------------------------------------------------------------

say "Готово"
cat <<EOF

  Mini App:   $TUNNEL_URL
  api:        http://localhost:$API_PORT/health
  режим web:  $([[ "$SYNC" == "on" ]] && printf 'sync → %s' "$API_ORIGIN" || printf 'local-only')
  логи:       $LOG_DIR/{web,api,bot}.log

EOF

if [[ "$SET_MENU_BUTTON" != "1" ]]; then
  printf '  Кнопку меню бота цей скрипт не чіпає. Або в @BotFather → /setmenubutton,\n'
  printf '  або перезапусти з прапорцем:  ./scripts/dev-start.sh --set-menu-button\n\n'
fi

if [[ "$SYNC" == "on" ]]; then
  ok "web зібрано з sync: Mini App автентифікується в api й може давати згоди."
else
  warn "web у local-only режимі — мережевих викликів немає, api й бот під час"
  warn "користування не задіяні. Для шляху Mini App → api → БД перезапусти так:"
  warn "  SYNC=on ./scripts/dev-start.sh"
fi
warn "Тексти згод — 0.9, контролер не названий; стенд приймає згоди лише в development."

printf '\nCtrl+C зупиняє все.\n'
wait
