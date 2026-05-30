#!/bin/bash
# Mac bot/.env + kod → VPS tek seferde (panel + signal runner).
set -euo pipefail
HOST="${1:-root@37.148.208.175}"
DIR="$(cd "$(dirname "$0")" && pwd)"
ENV="$DIR/.env"

read_env() {
  grep "^$1=" "$ENV" 2>/dev/null | cut -d= -f2- | tr -d ' ' || true
}

DASH_TOKEN="$(read_env DASHBOARD_TOKEN)"
SIGNAL_SYMBOLS="$(read_env SIGNAL_SYMBOLS)"
KLINE_INTERVAL="$(read_env KLINE_INTERVAL)"
SIGNAL_SYMBOLS="${SIGNAL_SYMBOLS:-BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT}"
KLINE_INTERVAL="${KLINE_INTERVAL:-5m}"

[ -n "$DASH_TOKEN" ] || echo "Uyari: DASHBOARD_TOKEN bos — panel girisi calismaz."

echo "=== VPS deploy (birlesik) → $HOST ==="
echo "  Panel token + SIGNAL_SYMBOLS + KLINE_INTERVAL Mac .env ile esitlenir"

echo "→ Dosyalar"
scp \
  "$DIR/webhook_server.py" \
  "$DIR/futures_client.py" \
  "$DIR/dashboard_static.html" \
  "$DIR/futures_signal_runner.py" \
  "$DIR/trade_logic.py" \
  "$DIR/strategy.py" \
  "$DIR/env_load.py" \
  "$DIR/logger.py" \
  "$DIR/activity_log.py" \
  "$DIR/systemd/tradebot-signals.service" \
  "$HOST:/root/bot/"

echo "→ VPS kurulum (tek SSH)"
ssh "$HOST" bash -s "$DASH_TOKEN" "$SIGNAL_SYMBOLS" "$KLINE_INTERVAL" <<'REMOTE'
set -euo pipefail
TOKEN="$1"
SIGS="$2"
INT="$3"
cd /root/bot

set_env() {
  local key="$1" val="$2"
  [ -n "$val" ] || return 0
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

set_env DASHBOARD_TOKEN "$TOKEN"
set_env SIGNAL_SYMBOLS "$SIGS"
set_env KLINE_INTERVAL "$INT"

cp -f /root/bot/tradebot-signals.service /etc/systemd/system/tradebot-signals.service
systemctl daemon-reload
systemctl enable tradebot-signals 2>/dev/null || true

systemctl restart tradebot
systemctl restart tradebot-signals
sleep 2

echo "--- tradebot (panel/webhook) ---"
systemctl is-active tradebot
echo "--- tradebot-signals (Yol A) ---"
systemctl is-active tradebot-signals
journalctl -u tradebot-signals -n 3 --no-pager
REMOTE

echo ""
echo "=== Tamam ==="
echo "  Panel:    http://37.148.208.175/dashboard"
echo "  Token:    bot/.env → DASHBOARD_TOKEN"
echo "  Log bot:  ssh $HOST journalctl -u tradebot-signals -f"
echo "  Mac'te futures_signal_runner CALISTIRMAYIN (cift emir)"
