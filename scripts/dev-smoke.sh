#!/usr/bin/env bash
# Перевіряє живий процес api, а не тести: стартує, віддає /health, відхиляє
# невалідний initData і не пише нічого зайвого в лог.
#
# Тести з testcontainers не доводять, що процес узагалі стартує з реального
# .env — цей скрипт доводить.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SMOKE_PORT:-8099}"
BASE="http://127.0.0.1:$PORT"
LOG="$(mktemp)"
FAILED=0

ok() { printf '\033[32m  ok\033[0m  %s\n' "$1"; }
bad() { printf '\033[31m FAIL\033[0m %s\n' "$1"; FAILED=1; }

[[ -f "$ROOT/.env" ]] || { echo "Немає .env — спершу ./scripts/dev-stand.sh" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ROOT/.env"
set +a
unset BOT_TOKEN

cd "$ROOT/apps/api"
uv run --locked uvicorn app.main:app_factory --factory --port "$PORT" \
  --no-access-log >"$LOG" 2>&1 &
API_PID=$!
cleanup() { kill "$API_PID" 2>/dev/null || true; rm -f "$LOG"; }
trap cleanup EXIT

printf 'Чекаю на api'
for _ in $(seq 1 30); do
  if curl -sf "$BASE/health" >/dev/null 2>&1; then break; fi
  kill -0 "$API_PID" 2>/dev/null || { printf '\n'; cat "$LOG"; exit 1; }
  printf '.'
  sleep 1
done
printf '\n\n'

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
body() { curl -s "$@"; }

[[ "$(body "$BASE/health")" == '{"status":"ok"}' ]] \
  && ok "GET /health → 200 ok" || bad "GET /health"

[[ "$(status -X POST "$BASE/v1/auth/telegram" -H 'Content-Type: application/json' \
  -d '{"init_data":"auth_date=1&user=%7B%22id%22%3A1%7D"}')" == "401" ]] \
  && ok "невалідний initData → 401" || bad "невалідний initData"

[[ "$(status "$BASE/v1/sessions")" == "401" ]] \
  && ok "сесії без токена → 401" || bad "сесії без токена"

[[ "$(status "$BASE/v1/entry/2026-03-14")" == "404" ]] \
  && ok "медичного ендпоінта немає → 404" || bad "медичний ендпоінт"

# §11 у двох частинах.
# 1) Запитаний шлях і вхідні значення не з'являються НІДЕ, включно з рядками
#    самого uvicorn — саме тому access-log вимкнений.
if grep -qE '2026-03-14|user=|auth_date=|signature=' "$LOG"; then
  bad "лог містить запитаний шлях або вхідні значення"
  grep -nE '2026-03-14|user=|auth_date=|signature=' "$LOG" | head -3
else
  ok "ні запитаного шляху, ні вхідних значень у логах"
fi

# 2) Адреса не потрапляє у власні структуровані рядки. Стартовий рядок uvicorn
#    "Uvicorn running on http://127.0.0.1:PORT" — це bind-адреса процесу, а не
#    адреса клієнта, тому перевіряється саме JSON.
if grep '^{' "$LOG" | grep -qE '127\.0\.0\.1|"ip"|"client"'; then
  bad "структурований лог містить адресу"
  grep '^{' "$LOG" | grep -nE '127\.0\.0\.1|"ip"|"client"' | head -3
else
  ok "адреси немає у структурованих рядках"
fi

# Кожен наш рядок має проходити allowlist §11 цілком.
if python3 - "$LOG" <<'PYCHECK'
import json, sys
ALLOWED = {
    "timestamp", "level", "event", "request_id", "route_template", "method",
    "status_code", "duration_ms", "account_ref", "record_count", "revision",
    "error_code", "retry_after",
}
bad = []
for line in open(sys.argv[1], encoding="utf-8"):
    if not line.startswith("{"):
        continue
    extra = set(json.loads(line)) - ALLOWED
    if extra:
        bad.append(sorted(extra))
if bad:
    print(bad[:3])
    sys.exit(1)
PYCHECK
then
  ok "кожен структурований рядок у межах allowlist §11"
else
  bad "структурований лог містить поле поза allowlist"
fi

if grep -q '"event": "consent_copy_unfrozen_accepted"' "$LOG"; then
  ok "стенд явно повідомляє, що копія згод незаморожена"
elif grep -q '"event": "consent_copy_unfrozen_refused"' "$LOG"; then
  ok "production-конфігурація: згоди відхиляються (503)"
else
  bad "немає жодного повідомлення про стан копії згод"
fi

printf '\n'
if [[ "$FAILED" == "0" ]]; then
  printf '\033[32mСтенд живий.\033[0m\n'
else
  printf '\033[31mСтенд стартував, але перевірки не пройшли.\033[0m\n'
  exit 1
fi
