import logging
from typing import List, Dict, Optional
import requests
import pandas as pd

logger = logging.getLogger("AlphaChain.BinanceFetcher")


class BinanceFetcher:
    BASE_URL: str = "https://api.binance.com/api/v3/klines"

    PAIR_MAP: Dict[str, str] = {
        "BTC/USD": "BTCUSDT",
        "ETH/USD": "ETHUSDT",
        "SOL/USD": "SOLUSDT",
        "BNB/USD": "BNBUSDT",
        "XRP/USD": "XRPUSDT",
        "DOGE/USD": "DOGEUSDT",
        "ADA/USD": "ADAUSDT",
        "AVAX/USD": "AVAXUSDT",
        "LINK/USD": "LINKUSDT",
        "NEAR/USD": "NEARUSDT",
        "SUI/USD": "SUIUSDT",
        "APT/USD": "APTUSDT",
    }

    INTERVAL_MAP: Dict[str, str] = {
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "AlphaChain-DataFetcher/1.0"})

    def to_binance_symbol(self, roostoo_pair: str) -> str:
        if roostoo_pair in self.PAIR_MAP:
            return self.PAIR_MAP[roostoo_pair]
        return roostoo_pair.replace("/", "").replace("USD", "USDT")

    def fetch_klines(
        self,
        roostoo_pair: str,
        interval: str = "15m",
        limit: int = 150,
    ) -> Optional[pd.DataFrame]:
        symbol = self.to_binance_symbol(roostoo_pair)
        binance_interval = self.INTERVAL_MAP.get(interval, interval)

        all_raw = []
        remaining = limit
        end_time = None

        try:
            while remaining > 0:
                batch_limit = min(remaining, 1000)
                params = {
                    "symbol": symbol,
                    "interval": binance_interval,
                    "limit": batch_limit,
                }
                if end_time is not None:
                    params["endTime"] = end_time

                response = self.session.get(self.BASE_URL, params=params, timeout=10.0)
                response.raise_for_status()
                raw_data = response.json()

                if not isinstance(raw_data, list) or len(raw_data) == 0:
                    break

                all_raw = raw_data + all_raw
                remaining -= len(raw_data)

                if len(raw_data) < batch_limit:
                    break

                earliest_open_time = raw_data[0][0]
                end_time = earliest_open_time - 1

            if len(all_raw) == 0:
                return None

            df = pd.DataFrame(
                all_raw,
                columns=[
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "close_time",
                    "quote_asset_volume",
                    "number_of_trades",
                    "taker_buy_base_asset_volume",
                    "taker_buy_quote_asset_volume",
                    "ignore",
                ],
            )

            df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
            df.rename(columns={"open_time": "timestamp"}, inplace=True)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df.sort_values("timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except requests.exceptions.RequestException as e:
            logger.error(f"Binance kline fetch failed for {roostoo_pair}: {e}")
            return None

    def fetch_universe_klines(
        self,
        pairs: List[str],
        interval: str = "15m",
        limit: int = 150,
    ) -> Dict[str, pd.DataFrame]:
        results: Dict[str, pd.DataFrame] = {}
        for pair in pairs:
            df = self.fetch_klines(pair, interval=interval, limit=limit)
            if df is not None and not df.empty:
                results[pair] = df
        return results
