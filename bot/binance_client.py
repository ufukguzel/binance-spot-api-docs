import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

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

    def get_account(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v3/account", signed=True)

    def get_price(self, symbol: str) -> float:
        data = self._request("GET", "/api/v3/ticker/price", params={"symbol": symbol})
        return float(data["price"])

    def get_closes(self, symbol: str, interval: str, limit: int) -> List[float]:
        data = self._request(
            "GET",
            "/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        return [float(candle[4]) for candle in data]

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        data = self._request("GET", "/api/v3/exchangeInfo", params={"symbol": symbol})
        symbols = data.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"Symbol not found: {symbol}")
        return symbols[0]

    def get_symbol_assets(self, symbol: str) -> Dict[str, str]:
        info = self.get_symbol_info(symbol)
        return {"base_asset": info["baseAsset"], "quote_asset": info["quoteAsset"]}

    def get_asset_free_balance(self, asset: str) -> float:
        account = self.get_account()
        for balance in account.get("balances", []):
            if balance.get("asset") == asset:
                return float(balance.get("free", "0"))
        return 0.0

    def _get_lot_size_step(self, symbol_info: Dict[str, Any]) -> Decimal:
        for f in symbol_info.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                return Decimal(f["stepSize"])
        raise RuntimeError("LOT_SIZE filter missing for symbol")

    def get_lot_size_step(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        return float(self._get_lot_size_step(info))

    def _round_down_step(self, value: float, step: Decimal) -> float:
        val = Decimal(str(value))
        qty = (val / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(qty)

    def get_sell_quantity_from_balance(self, symbol: str, sell_pct: float = 99.5) -> float:
        symbol_info = self.get_symbol_info(symbol)
        base_asset = symbol_info["baseAsset"]
        step = self._get_lot_size_step(symbol_info)
        free_balance = self.get_asset_free_balance(base_asset)
        qty = free_balance * max(0.0, min(100.0, sell_pct)) / 100.0
        rounded = self._round_down_step(qty, step)
        if rounded <= 0:
            raise RuntimeError(
                f"Calculated sell quantity is zero. base_asset={base_asset} free={free_balance}"
            )
        return rounded

    def create_market_order(
        self,
        symbol: str,
        side: str,
        quote_order_qty: Optional[float] = None,
        quantity: Optional[float] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "newOrderRespType": "RESULT",
        }
        if quote_order_qty is None and quantity is None:
            raise RuntimeError("Either quote_order_qty or quantity must be provided")
        if quote_order_qty is not None and quantity is not None:
            raise RuntimeError("Use only one of quote_order_qty or quantity")
        if quote_order_qty is not None:
            payload["quoteOrderQty"] = f"{quote_order_qty:.8f}"
        if quantity is not None:
            payload["quantity"] = f"{quantity:.8f}"
        endpoint = "/api/v3/order/test" if dry_run else "/api/v3/order"
        return self._request("POST", endpoint, params=payload, signed=True)
