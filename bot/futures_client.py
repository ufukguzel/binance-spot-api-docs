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

    def get_symbol_step(self, symbol: str) -> Decimal:
        data = self._request("GET", "/fapi/v1/exchangeInfo", params={"symbol": symbol})
        symbols = data.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"Symbol not found: {symbol}")
        for f in symbols[0].get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                return Decimal(f["stepSize"])
        raise RuntimeError("LOT_SIZE filter missing for symbol")

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
            "quantity": f"{qty:.8f}",
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            payload["reduceOnly"] = "true"
        endpoint = "/fapi/v1/order/test" if dry_run else "/fapi/v1/order"
        return self._request("POST", endpoint, params=payload, signed=True)

