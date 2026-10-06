import logging
from typing import List, Optional
import pandas as pd

from config.trading_config import trading_config
from data.candle_manager import CandleManager
from strategy.regime_detector import RegimeDetector, MarketRegime
from strategy.linear_factor_model import CryptoLinearFactorModel
from strategy.signals import SignalAction, TradeSignal

logger = logging.getLogger("AlphaChain.Strategy")

__all__ = ["MultiRegimeAlphaStrategy", "TradeSignal", "SignalAction"]


class RobustCandleManager(CandleManager):
    """CandleManager proxy supporting direct pair key indexing in candles dict."""

    def get_candles(self, pair: str, timeframe: str = "15m") -> Optional[pd.DataFrame]:
        if pair in self.candles and isinstance(self.candles[pair], pd.DataFrame):
            return self.candles[pair]
        return super().get_candles(pair, timeframe)

    def get_latest_price(self, pair: str) -> Optional[float]:
        val = super().get_latest_price(pair)
        if val is not None:
            return val
        if pair in self.candles and isinstance(self.candles[pair], pd.DataFrame) and not self.candles[pair].empty:
            return float(self.candles[pair]["close"].iloc[-1])
        return None


class MultiRegimeAlphaStrategy:
    def __init__(
        self,
        regime_detector: Optional[RegimeDetector] = None,
        linear_model: Optional[CryptoLinearFactorModel] = None,
        candle_manager: Optional[CandleManager] = None,
    ):
        self.candle_manager: CandleManager = candle_manager or RobustCandleManager()
        self.regime_detector: RegimeDetector = regime_detector or RegimeDetector()
        self.linear_model: CryptoLinearFactorModel = linear_model or CryptoLinearFactorModel(
            cost_hurdle=0.0014,
            max_positions=trading_config.MAX_CONCURRENT_POSITIONS,
            cooldown_bars=12,
        )

    def generate_signals(
        self,
        candle_manager: Optional[CandleManager] = None,
        active_pairs: Optional[List[str]] = None,
        current_bar_index: int = 0,
    ) -> List[TradeSignal]:
        cm = candle_manager or self.candle_manager
        active_set = set(active_pairs or [])

        btc_15 = cm.get_candles("BTC/USD", timeframe="15m")
        if btc_15 is not None and len(btc_15) >= 2:
            last_btc_ret = (btc_15["close"].iloc[-1] - btc_15["open"].iloc[-1]) / btc_15["open"].iloc[-1]
            if last_btc_ret < -0.008:
                return []

        regime_result = self.regime_detector.detect_regime(cm, timeframe="1h")
        regime = regime_result.regime

        candidate_signals = self.linear_model.generate_signals(
            candle_manager=cm,
            active_pairs=list(active_set),
            timeframe="1h",
            current_bar_index=current_bar_index,
        )

        filtered_signals: List[TradeSignal] = []

        for sig in candidate_signals:
            if sig.action == SignalAction.BUY:
                if regime == MarketRegime.BULL:
                    # In BULL: pass all qualifying signals
                    filtered_signals.append(sig)
                elif regime == MarketRegime.CHOP:
                    # In CHOP: allow conviction >= 0.40
                    if sig.conviction >= 0.40:
                        filtered_signals.append(sig)
                elif regime == MarketRegime.BEAR:
                    # In BEAR: contract long exposure to 100% cash defense
                    continue
            elif sig.action == SignalAction.SHORT_OPEN:
                if regime == MarketRegime.BEAR and sig.conviction >= 0.55:
                    filtered_signals.append(sig)
                elif regime == MarketRegime.CHOP and sig.conviction >= 0.45:
                    filtered_signals.append(sig)

        filtered_signals.sort(key=lambda s: s.conviction, reverse=True)

        if getattr(trading_config, "DYNAMIC_CONCURRENCY_ENABLED", True):
            return filtered_signals
        return filtered_signals[:trading_config.MAX_CONCURRENT_POSITIONS]

    def record_exit(self, pair: str, current_bar_index: int, net_pnl: float = 0.0) -> None:
        self.linear_model.record_exit(pair, current_bar_index, net_pnl)
