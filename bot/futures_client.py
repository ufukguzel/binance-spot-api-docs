import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests


class FuturesClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._step_cache: Dict[str, Decimal] = {}

    def _sign(self, params: Dict[str, Any]) -> str:
        query = urlencode(params, doseq=True)
        return hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params["signature"] = self._sign(params)

        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, params=params, timeout=15)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise RuntimeError(f"HTTP {response.status_code}: {detail}") from None
        if response.text:
            return response.json()
        return {}

    def get_position_amt(self, symbol: str) -> float:
        data = self._request("GET", "/fapi/v2/positionRisk", params={"symbol": symbol}, signed=True)
        if not data:
            return 0.0
        return float(data[0]["positionAmt"])

    def get_open_positions(self) -> list:
        data = self._request("GET", "/fapi/v2/positionRisk", signed=True)
        if not isinstance(data, list):
            return []
        positions = []
        for p in data:
            amt = float(p.get("positionAmt", 0))
            if amt == 0:
                continue
            positions.append(
                {
                    "symbol": p.get("symbol"),
                    "side": "LONG" if amt > 0 else "SHORT",
                    "position_amt": amt,
                    "amount": abs(amt),
                    "entry_price": p.get("entryPrice"),
                    "mark_price": p.get("markPrice"),
                    "unrealized_pnl": p.get("unRealizedProfit"),
                    "leverage": p.get("leverage"),
                    "liquidation_price": p.get("liquidationPrice"),
                }
            )
        positions.sort(key=lambda x: str(x.get("symbol", "")))
        return positions

    def get_open_orders(self) -> list:
        data = self._request("GET", "/fapi/v1/openOrders", signed=True)
        return data if isinstance(data, list) else []

    def get_account_summary(self) -> Dict[str, Any]:
        """Futures USDT-M hesap özeti (demo veya canlı base_url)."""
        acc = self._request("GET", "/fapi/v2/account", signed=True)
        if not isinstance(acc, dict):
            acc = {}

        def _f(key: str) -> float:
            try:
                return float(acc.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        wallet = _f("totalWalletBalance")
        unrealized = _f("totalUnrealizedProfit")
        margin = _f("totalMarginBalance")
        available = _f("availableBalance")
        maint_margin = _f("totalMaintMargin")
        initial_margin = _f("totalInitialMargin")
        equity = margin if margin else wallet + unrealized
        margin_ratio_pct = (maint_margin / margin * 100.0) if margin > 0 else 0.0

        usdt_asset: Dict[str, Any] = {}
        for a in acc.get("assets") or []:
            if not isinstance(a, dict) or a.get("asset") != "USDT":
                continue
            usdt_asset = {
                "balance": float(a.get("walletBalance", 0) or 0),
                "unrealized_profit": float(a.get("unrealizedProfit", 0) or 0),
                "margin_balance": float(a.get("marginBalance", 0) or 0),
                "available": float(a.get("availableBalance", 0) or 0),
                "maint_margin": float(a.get("maintMargin", 0) or 0),
            }
            break

        now_ms = int(time.time() * 1000)
        day_ms = 24 * 60 * 60 * 1000
        week_ms = 7 * day_ms
        realized_today = self._sum_realized_pnl(now_ms - day_ms)
        realized_7d = self._sum_realized_pnl(now_ms - week_ms)

        assets = []
        for a in acc.get("assets") or []:
            if not isinstance(a, dict):
                continue
            bal = float(a.get("walletBalance", 0) or 0)
            if bal == 0 and float(a.get("unrealizedProfit", 0) or 0) == 0:
                continue
            assets.append(
                {
                    "asset": a.get("asset"),
                    "wallet_balance": bal,
                    "unrealized_profit": float(a.get("unrealizedProfit", 0) or 0),
                    "margin_balance": float(a.get("marginBalance", 0) or 0),
                }
            )

        return {
            "equity_usdt": equity,
            "wallet_balance_usdt": wallet,
            "margin_balance_usdt": margin,
            "available_balance_usdt": available,
            "unrealized_pnl_usdt": unrealized,
            "maintenance_margin_usdt": maint_margin,
            "initial_margin_usdt": initial_margin,
            "margin_ratio_pct": round(margin_ratio_pct, 4),
            "realized_pnl_today_usdt": realized_today,
            "realized_pnl_7d_usdt": realized_7d,
            "can_trade": bool(acc.get("canTrade", True)),
            "can_deposit": bool(acc.get("canDeposit", True)),
            "assets": assets,
            "usdt": usdt_asset,
        }

    def _sum_realized_pnl(self, start_time_ms: int) -> float:
        total = 0.0
        start = int(start_time_ms)
        while True:
            batch = self._request(
                "GET",
                "/fapi/v1/income",
                params={
                    "incomeType": "REALIZED_PNL",
                    "startTime": start,
                    "limit": 1000,
                },
                signed=True,
            )
            if not isinstance(batch, list) or not batch:
                break
            for row in batch:
                try:
                    total += float(row.get("income", 0) or 0)
                except (TypeError, ValueError):
                    pass
            if len(batch) < 1000:
                break
            last_time = int(batch[-1].get("time", 0) or 0)
            if last_time <= start:
                break
            start = last_time + 1
        return total

    def get_klines(self, symbol: str, interval: str = "1m", limit: int = 120) -> list:
        allowed = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}
        if interval not in allowed:
            raise RuntimeError(f"interval must be one of {sorted(allowed)}")
        limit = max(10, min(int(limit), 500))
        raw = self._request(
            "GET",
            "/fapi/v1/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        if not isinstance(raw, list):
            return []
        candles = []
        for row in raw:
            candles.append(
                {
                    "time": int(row[0]) // 1000,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        return candles

    def get_symbol_step(self, symbol: str) -> Decimal:
        if symbol in self._step_cache:
            return self._step_cache[symbol]
        data = self._request("GET", "/fapi/v1/exchangeInfo", params={"symbol": symbol})
        symbols = data.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"Symbol not found: {symbol}")
        for f in symbols[0].get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                step = Decimal(f["stepSize"])
                self._step_cache[symbol] = step
                return step
        raise RuntimeError("LOT_SIZE filter missing for symbol")

    @staticmethod
    def _qty_param(qty: float) -> str:
        d = Decimal(str(qty)).normalize()
        text = format(d, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def round_qty(self, symbol: str, qty: float) -> float:
        step = self.get_symbol_step(symbol)
        val = Decimal(str(qty))
        rounded = (val / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(rounded)

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        dry_run: bool,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        qty = self.round_qty(symbol, quantity)
        if qty <= 0:
            raise RuntimeError("Calculated quantity is zero")
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self._qty_param(qty),
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            payload["reduceOnly"] = "true"
        endpoint = "/fapi/v1/order/test" if dry_run else "/fapi/v1/order"
        return self._request("POST", endpoint, params=payload, signed=True)

