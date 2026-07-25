#!/usr/bin/env bash
# Підіймає весь стенд однією командою: PostgreSQL, міграції, web, api, бот.
#
# URL тунелю береться з ngrok автоматично (його локальний API на :4040), тож
# копіювати адресу руками не треба. Ctrl+C гасить усе.
#
# BOT_TOKEN лишається виключно в apps/bot/.env: кожен процес запускається у
# власному підшелі, і оточення api його не бачить (§5.3).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
BOT_ENV_FILE="$ROOT/apps/bot/.env"
LOG_DIR="$ROOT/.dev-logs"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
SET_MENU_BUTTON=0

[[ "${1:-}" == "--set-menu-button" ]] && SET_MENU_BUTTON=1

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok() { printf '\033[32m  ok\033[0m  %s\n' "$1"; }
warn() { printf '\033[33m  ⚠ \033[0m %s\n' "$1"; }
die() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

mkdir -p "$LOG_DIR"

# --- тунель -----------------------------------------------------------------

say "Шукаю тунель"
TUNNEL_URL="$(curl -s --max-time 5 http://127.0.0.1:4040/api/tunnels 2>/dev/null |
  python3 -c "
import json, sys
try:
    tunnels = json.load(sys.stdin).get('tunnels', [])
except Exception:
    sys.exit()
for t in tunnels:
    if t.get('proto') == 'https' and '$WEB_PORT' in t.get('config', {}).get('addr', ''):
        print(t['public_url'])
        break
" || true)"

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

# web
(cd "$ROOT/apps/web" && pnpm dev --port "$WEB_PORT" >"$LOG_DIR/web.log" 2>&1) &
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
  логи:       $LOG_DIR/{web,api,bot}.log

EOF

if [[ "$SET_MENU_BUTTON" != "1" ]]; then
  printf '  Кнопку меню бота цей скрипт не чіпає. Або в @BotFather → /setmenubutton,\n'
  printf '  або перезапусти з прапорцем:  ./scripts/dev-start.sh --set-menu-button\n\n'
fi

warn "web ще не робить мережевих викликів — сервер під час користування не задіяний."
warn "Тексти згод — 0.9, контролер не названий; стенд приймає згоди лише в development."

printf '\nCtrl+C зупиняє все.\n'
wait
