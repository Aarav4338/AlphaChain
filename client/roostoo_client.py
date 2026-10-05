import time
import hmac
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
import requests

from config.settings import settings
from config.trading_config import trading_config
from client.rate_limiter import RateLimiter

logger = logging.getLogger("AlphaChain.RoostooClient")


class RoostooClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_calls_per_min: Optional[int] = None,
    ):
        self.api_key: str = api_key or settings.ROOSTOO_API_KEY
        self.secret_key: str = secret_key or settings.ROOSTOO_SECRET_KEY
        self.base_url: str = (base_url or settings.ROOSTOO_BASE_URL).rstrip("/")

        rate_limit = max_calls_per_min or trading_config.MAX_API_CALLS_PER_MINUTE
        self.rate_limiter: RateLimiter = RateLimiter(max_calls=rate_limit, period_seconds=60.0)

        self.session: requests.Session = requests.Session()
        self.session.headers.update({"User-Agent": "AlphaChain-Bot/1.0"})

    @staticmethod
    def get_timestamp() -> str:
        return str(int(time.time() * 1000))

    def _sign_payload(self, payload: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
        payload["timestamp"] = self.get_timestamp()
        sorted_keys = sorted(payload.keys())
        total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            total_params.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": signature,
        }
        return headers, total_params

    def _send_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        req_headers = headers.copy() if headers else {}

        if not self.rate_limiter.acquire(tokens_needed=1.0, timeout=30.0):
            logger.error(f"Rate limiter timeout reached before request to {path}")
            return None

        for attempt in range(1, max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, params=params, headers=req_headers, timeout=10.0)
                elif method.upper() == "POST":
                    response = self.session.post(url, data=data, headers=req_headers, timeout=10.0)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 429:
                    time.sleep((2 ** attempt) + 1.0)
                    continue

                if response.status_code >= 500:
                    time.sleep((2 ** attempt) + 0.5)
                    continue

                response.raise_for_status()
                result = response.json()

                if isinstance(result, dict) and not result.get("Success", True):
                    err_msg = result.get("ErrMsg", "Unknown API error")
                    logger.warning(f"Roostoo API error on {path}: {err_msg}")

                return result

            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    time.sleep((2 ** attempt) + 0.5)
                else:
                    logger.error(f"Request failed ({attempt}/{max_retries}) on {path}: {e}")

        return None

    def check_server_time(self) -> Optional[Dict[str, Any]]:
        return self._send_request("GET", "/v3/serverTime")

    def get_exchange_info(self) -> Optional[Dict[str, Any]]:
        return self._send_request("GET", "/v3/exchangeInfo")

    def get_ticker(self, pair: Optional[str] = None) -> Optional[Dict[str, Any]]:
        params = {"timestamp": self.get_timestamp()}
        if pair:
            params["pair"] = pair
        return self._send_request("GET", "/v3/ticker", params=params)

    def get_balance(self) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        headers, _ = self._sign_payload(payload)
        return self._send_request("GET", "/v3/balance", params=payload, headers=headers)

    def get_pending_count(self) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        headers, _ = self._sign_payload(payload)
        return self._send_request("GET", "/v3/pending_count", params=payload, headers=headers)

    def place_order(
        self,
        pair: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if order_type is None:
            order_type = "LIMIT" if price is not None else "MARKET"

        if order_type.upper() == "LIMIT" and price is None:
            raise ValueError("LIMIT orders require a price.")

        payload = {
            "pair": pair,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(quantity),
        }
        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = str(price)

        headers, total_params = self._sign_payload(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._send_request("POST", "/v3/place_order", data=total_params, headers=headers)

    def query_order(
        self,
        order_id: Optional[int] = None,
        pair: Optional[str] = None,
        pending_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        elif pair:
            payload["pair"] = pair
            if pending_only:
                payload["pending_only"] = "TRUE"

        headers, total_params = self._sign_payload(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._send_request("POST", "/v3/query_order", data=total_params, headers=headers)

    def cancel_order(
        self,
        order_id: Optional[int] = None,
        pair: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        if order_id is not None:
            payload["order_id"] = str(order_id)
        elif pair:
            payload["pair"] = pair

        headers, total_params = self._sign_payload(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._send_request("POST", "/v3/cancel_order", data=total_params, headers=headers)

    def short_open(
        self,
        pair: str,
        collateral: float,
        price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        payload = {
            "pair": pair,
            "collateral": str(collateral),
        }
        if price is not None:
            payload["order_type"] = "LIMIT"
            payload["price"] = str(price)

        headers, total_params = self._sign_payload(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._send_request("POST", "/v6/short_open", data=total_params, headers=headers)

    def short_close(
        self,
        pair: str,
        close_qty: Optional[float] = None,
        close_pct: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        payload = {"pair": pair}
        if close_qty is not None:
            payload["close_qty"] = str(close_qty)
        elif close_pct is not None:
            payload["close_pct"] = str(close_pct)

        headers, total_params = self._sign_payload(payload)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self._send_request("POST", "/v6/short_close", data=total_params, headers=headers)

    def get_short_positions(self) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {}
        headers, _ = self._sign_payload(payload)
        return self._send_request("GET", "/v6/short_positions", params=payload, headers=headers)
