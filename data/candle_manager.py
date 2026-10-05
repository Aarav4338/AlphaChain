import logging
from typing import Dict, Optional, Tuple
import pandas as pd

logger = logging.getLogger("AlphaChain.CandleManager")


class CandleManager:
    TIMEFRAME_SECONDS: Dict[str, int] = {
        "15m": 15 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
        "1d": 24 * 60 * 60,
    }

    def __init__(self, max_buffer_size: int = 200):
        self.max_buffer_size: int = max_buffer_size
        self.candles: Dict[str, Dict[str, pd.DataFrame]] = {
            "15m": {},
            "1h": {},
        }
        self.latest_ticks: Dict[str, Dict[str, float]] = {}

    def initialize_pair_history(self, pair: str, timeframe: str, df: pd.DataFrame) -> None:
        if df.empty:
            return

        cleaned_df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(cleaned_df["timestamp"]):
            cleaned_df["timestamp"] = pd.to_datetime(cleaned_df["timestamp"], utc=True)

        for col in ["open", "high", "low", "close", "volume"]:
            cleaned_df[col] = pd.to_numeric(cleaned_df[col], errors="coerce")

        cleaned_df.sort_values("timestamp", inplace=True)
        if len(cleaned_df) > self.max_buffer_size:
            cleaned_df = cleaned_df.iloc[-self.max_buffer_size:].copy()

        cleaned_df.reset_index(drop=True, inplace=True)

        if timeframe not in self.candles:
            self.candles[timeframe] = {}
        self.candles[timeframe][pair] = cleaned_df

        latest_row = cleaned_df.iloc[-1]
        self.latest_ticks[pair] = {
            "price": float(latest_row["close"]),
            "bid": float(latest_row["close"]),
            "ask": float(latest_row["close"]),
            "timestamp": latest_row["timestamp"],
        }

    def update_tick(
        self,
        pair: str,
        last_price: float,
        max_bid: Optional[float] = None,
        min_ask: Optional[float] = None,
        timestamp: Optional[pd.Timestamp] = None,
    ) -> None:
        now = timestamp or pd.Timestamp.now(tz="UTC")
        if now.tzinfo is None:
            now = now.tz_localize("UTC")

        self.latest_ticks[pair] = {
            "price": last_price,
            "bid": max_bid if max_bid is not None else last_price,
            "ask": min_ask if min_ask is not None else last_price,
            "timestamp": now,
        }

        for timeframe, period_sec in self.TIMEFRAME_SECONDS.items():
            if timeframe not in self.candles or pair not in self.candles[timeframe]:
                continue

            df = self.candles[timeframe][pair]
            if df.empty:
                continue

            now_epoch_sec = int(now.timestamp())
            candle_start_epoch_sec = (now_epoch_sec // period_sec) * period_sec
            current_candle_start = pd.to_datetime(candle_start_epoch_sec, unit="s", utc=True)

            last_candle_start = df.iloc[-1]["timestamp"]

            if last_candle_start == current_candle_start:
                idx = df.index[-1]
                df.at[idx, "high"] = max(df.at[idx, "high"], last_price)
                df.at[idx, "low"] = min(df.at[idx, "low"], last_price)
                df.at[idx, "close"] = last_price
            elif now > last_candle_start:
                new_row = pd.DataFrame([{
                    "timestamp": current_candle_start,
                    "open": last_price,
                    "high": last_price,
                    "low": last_price,
                    "close": last_price,
                    "volume": 0.0,
                }])
                df = pd.concat([df, new_row], ignore_index=True)

                if len(df) > self.max_buffer_size:
                    df = df.iloc[-self.max_buffer_size:].copy().reset_index(drop=True)

                self.candles[timeframe][pair] = df

    def get_candles(self, pair: str, timeframe: str = "15m") -> Optional[pd.DataFrame]:
        return self.candles.get(timeframe, {}).get(pair)

    def get_latest_price(self, pair: str) -> Optional[float]:
        tick = self.latest_ticks.get(pair)
        return tick["price"] if tick else None

    def get_latest_bid_ask(self, pair: str) -> Optional[Tuple[float, float]]:
        tick = self.latest_ticks.get(pair)
        if tick and "bid" in tick and "ask" in tick:
            return tick["bid"], tick["ask"]
        return None
