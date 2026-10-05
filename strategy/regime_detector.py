from enum import Enum
from dataclasses import dataclass
import logging
from typing import Optional

from config.trading_config import trading_config
from data.candle_manager import CandleManager
from data.indicators import compute_ema, compute_adx

logger = logging.getLogger("AlphaChain.RegimeDetector")


class MarketRegime(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    CHOP = "CHOP"


@dataclass
class RegimeResult:
    regime: MarketRegime
    btc_adx: float
    btc_trend_score: float
    rationale: str


class RegimeDetector:
    def __init__(self, benchmark_pair: Optional[str] = None):
        self.benchmark_pair: str = benchmark_pair or trading_config.REGIME_BENCHMARK

    def detect_regime(self, candle_manager: CandleManager, timeframe: str = "1h") -> RegimeResult:
        df = candle_manager.get_candles(self.benchmark_pair, timeframe=timeframe)

        if df is None or len(df) < 50:
            return RegimeResult(
                regime=MarketRegime.CHOP,
                btc_adx=0.0,
                btc_trend_score=0.0,
                rationale="Insufficient historical data",
            )

        close = df["close"]
        ema_fast = compute_ema(close, trading_config.EMA_FAST)
        ema_slow = compute_ema(close, trading_config.EMA_SLOW)
        ema_base = compute_ema(close, min(len(close), trading_config.EMA_BASELINE))

        last_close = close.iloc[-1]
        last_fast = ema_fast.iloc[-1]
        last_slow = ema_slow.iloc[-1]
        last_base = ema_base.iloc[-1]

        adx_df = compute_adx(df, period=trading_config.ADX_PERIOD)
        last_adx = adx_df["adx"].iloc[-1]
        last_plus_di = adx_df["plus_di"].iloc[-1]
        last_minus_di = adx_df["minus_di"].iloc[-1]

        trend_score = 0.0
        if last_close > last_fast > last_slow:
            trend_score += 0.5
            if last_slow > last_base:
                trend_score += 0.5
        elif last_close < last_fast < last_slow:
            trend_score -= 0.5
            if last_slow < last_base:
                trend_score -= 0.5

        is_trending = last_adx >= trading_config.ADX_TREND_THRESHOLD

        if is_trending and trend_score >= 0.5 and last_plus_di > last_minus_di:
            regime = MarketRegime.BULL
            rationale = f"Bullish: BTC > EMA{trading_config.EMA_FAST} with ADX={last_adx:.1f}"
        elif is_trending and trend_score <= -0.5 and last_minus_di > last_plus_di:
            regime = MarketRegime.BEAR
            rationale = f"Bearish: BTC < EMA{trading_config.EMA_FAST} with ADX={last_adx:.1f}"
        else:
            regime = MarketRegime.CHOP
            rationale = f"Chop: ADX={last_adx:.1f}"

        logger.info(f"Market Regime Detected: {regime.value} | ADX: {last_adx:.1f} | Score: {trend_score:.2f}")
        return RegimeResult(
            regime=regime,
            btc_adx=float(last_adx),
            btc_trend_score=float(trend_score),
            rationale=rationale,
        )
