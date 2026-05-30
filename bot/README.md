# Futures MA Bot (Demo)

Binance **Demo Futures** üzerinde MA kesişimi sinyali ve reverse emir mantığı.

## Bileşenler

| Dosya | Görev |
|--------|--------|
| `futures_signal_runner.py` | Mum verisinden sinyal + emir (VPS’te çalışır) |
| `webhook_server.py` | Kontrol paneli API + `/dashboard` |
| `trade_logic.py` | Ortak emir mantığı |
| `deploy-vps.sh` | Kod + systemd → VPS |

## Kurulum

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

## Çalıştırma

```bash
# Emir üreten süreç (VPS)
python futures_signal_runner.py

# Panel (aynı makinede, emir üretmez)
python webhook_server.py
```

Panel: `http://<sunucu>/dashboard` — `DASHBOARD_TOKEN` ile giriş.

**Mac’te `futures_signal_runner` çalıştırmayın**; VPS’te zaten çalışıyorsa çift emir olur.

## Deploy

```bash
./deploy-vps.sh
```

Demo emir: `DRY_RUN=false` ve `ALLOW_LIVE_ORDERS=true`.
