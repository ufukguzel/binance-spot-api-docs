# Binance Futures Reverse Bot (Demo)

Bu klasor, Binance **Demo Futures** (`demo-fapi.binance.com`) ile calisan reverse (ters yone gecisli) webhook botudur.

## Ozellikler

- SMA crossover stratejisi (`SHORT_WINDOW`, `LONG_WINDOW`)
- Risk limiti (`MAX_DAILY_LOSS_PCT`)
- Piyasa verisi (`/api/v3/klines`, `/api/v3/ticker/price`)
- Emir gonderimi (`/fapi/v1/order/test` veya `/fapi/v1/order`)
- Varsayilan guvenli mod: `DRY_RUN=true`
- Canli demo emir kilidi: `ALLOW_LIVE_ORDERS=false` (yanlislikla emir acilmasin diye)
- TradingView webhook entegrasyonu (`/tradingview/webhook`)
- Reverse mantik: BUY sinyalinde short kapanir+long acilir; SELL sinyalinde long kapanir+short acilir.

## Kurulum

1. Python 3.10+ kullan.
2. Bagimliliklari kur:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r bot/requirements.txt
```

3. Ornek env dosyasini kopyala:

```bash
cp bot/.env.example bot/.env
```

4. `bot/.env` icine API bilgilerini gir (`demo.binance.com` API Management; varsayilan `BASE_URL` demo futures endpoint).

## Calistirma

```bash
python bot/main.py
```

## TradingView Alarm Entegrasyonu

Webhook sunucusunu calistir:

```bash
python bot/webhook_server.py
```

Health kontrol:

```bash
curl http://localhost:8080/health
```

TradingView webhook URL:

```text
http://<senin-sunucun>:8080/tradingview/webhook
```

TradingView alarm mesaji (JSON):

```json
{
  "secret": "change_me",
  "action": "BUY",
  "symbol": "BTCUSDT",
  "quantity": 0.002
}
```

`action` sadece `BUY` veya `SELL` olabilir. `symbol` opsiyoneldir.
- `quantity` gonderebilirsin; gonderilmezse `.env` icindeki `FUTURES_QTY` kullanilir.
- Coklu coin icin `.env` icine `SYMBOL_QTY_MAP` koyabilirsin; mesajda `quantity` yoksa coin bazli miktar otomatik secilir.
- Ayni yonde sinyal gelirse tekrar emir acmaz (already long/already short).
Gercek demo emir icin iki ayar birlikte gerekli: `DRY_RUN=false` ve `ALLOW_LIVE_ORDERS=true`.

Ornek:
```env
SYMBOL_QTY_MAP={"BTCUSDT":0.002,"ETHUSDT":0.01,"BNBUSDT":0.05}
```

## Onemli Notlar

- Ilk asamada mutlaka test/demo ortaminda kal.
- `DRY_RUN=false` yapmadan once miktar ve risk degerlerini dusuk tut.
- Gercek para ile islemde her zaman ek risk kontrolleri ve kill-switch kullan.
- Webhook endpointini internete aciyorsan mutlaka guclu `WEBHOOK_SECRET` kullan.
