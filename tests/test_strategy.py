"""
Unit tests for RegimeDetector and MultiRegimeAlphaStrategy.
"""
import pytest
import pandas as pd
import numpy as np

from data.candle_manager import CandleManager
from strategy.regime_detector import RegimeDetector, MarketRegime
from strategy.multi_regime_alpha import MultiRegimeAlphaStrategy
from strategy.signals import SignalAction


def make_candle_df(n: int, trend_slope: float, vol: float = 0.005) -> pd.DataFrame:
    """Generates synthetic candles with an upward or downward trend."""
    np.random.seed(42)
    base = 100.0
    returns = np.random.normal(trend_slope, vol, n)
    prices = base * np.cumprod(1 + returns)
    timestamps = pd.date_range("2026-10-01", periods=n, freq="15min", tz="UTC")

    highs = prices * (1 + np.abs(np.random.normal(0.002, 0.001, n)))
    lows = prices * (1 - np.abs(np.random.normal(0.002, 0.001, n)))
    opens = prices * (1 + np.random.normal(0.0, 0.001, n))
    volumes = np.random.uniform(50, 150, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    })


def test_regime_detector():
    """Verifies that the regime detector accurately identifies Bull, Bear, and Chop markets."""
    manager = CandleManager(max_buffer_size=150)
    detector = RegimeDetector(benchmark_pair="BTC/USD")

    # 1. Strong Bull trend (positive drift)
    bull_df = make_candle_df(100, trend_slope=0.004)
    manager.initialize_pair_history("BTC/USD", "1h", bull_df)
    res = detector.detect_regime(manager, timeframe="1h")
    assert res.regime == MarketRegime.BULL
    assert res.btc_trend_score > 0

    # 2. Strong Bear trend (negative drift)
    bear_df = make_candle_df(100, trend_slope=-0.004)
    manager.initialize_pair_history("BTC/USD", "1h", bear_df)
    res = detector.detect_regime(manager, timeframe="1h")
    assert res.regime == MarketRegime.BEAR
    assert res.btc_trend_score < 0

    # 3. Chop market (flat drift, mean-reverting noise)
    chop_df = make_candle_df(100, trend_slope=0.0, vol=0.001)
    manager.initialize_pair_history("BTC/USD", "1h", chop_df)
    res = detector.detect_regime(manager, timeframe="1h")
    assert res.regime == MarketRegime.CHOP


def test_multi_regime_alpha_strategy():
    """Verifies signal generation in a Bull regime."""
    manager = CandleManager(max_buffer_size=150)

    # Setup BTC in Bull trend
    btc_df = make_candle_df(100, trend_slope=0.002, vol=0.008)
    manager.initialize_pair_history("BTC/USD", "1h", btc_df)
    manager.initialize_pair_history("BTC/USD", "15m", btc_df)

    # Setup SOL as a strong outperformer with realistic volatility
    sol_df = make_candle_df(100, trend_slope=0.004, vol=0.008)
    manager.initialize_pair_history("SOL/USD", "15m", sol_df)
    manager.initialize_pair_history("SOL/USD", "1h", sol_df)

    eth_df = make_candle_df(100, trend_slope=0.001, vol=0.008)
    manager.initialize_pair_history("ETH/USD", "15m", eth_df)
    manager.initialize_pair_history("ETH/USD", "1h", eth_df)

    doge_df = make_candle_df(100, trend_slope=-0.001, vol=0.008)
    manager.initialize_pair_history("DOGE/USD", "15m", doge_df)
    manager.initialize_pair_history("DOGE/USD", "1h", doge_df)

    strategy = MultiRegimeAlphaStrategy()
    signals = strategy.generate_signals(manager, active_pairs=[])

    # Should generate at least one BUY signal in Bull regime
    assert len(signals) > 0
    top_signal = signals[0]
    assert top_signal.action == SignalAction.BUY
    assert top_signal.stop_loss_price < top_signal.entry_price
    assert top_signal.take_profit_price > top_signal.entry_price
    assert top_signal.atr > 0


def test_us_equity_decoupled_from_btc_chop():
    """
    GIVEN: Bitcoin is in dead CHOP (flat drift, low ADX).
    GIVEN: A US Equity (METAB/USD) is in a clean bullish uptrend.
    EXPECTED: METAB generates an allowed BUY signal, decoupled from Bitcoin's chop gate.
    """
    from config.trading_config import trading_config
    trading_config.apply_profile("bulletproof_fortress")

    manager = CandleManager(max_buffer_size=150)

    # Setup BTC in flat CHOP
    btc_chop_df = make_candle_df(100, trend_slope=0.0, vol=0.001)
    manager.initialize_pair_history("BTC/USD", "1h", btc_chop_df)
    manager.initialize_pair_history("BTC/USD", "15m", btc_chop_df)

    # Setup METAB in strong bull trend
    meta_df = make_candle_df(100, trend_slope=0.004, vol=0.005)
    manager.initialize_pair_history("METAB/USD", "1h", meta_df)
    manager.initialize_pair_history("METAB/USD", "15m", meta_df)

    # Setup SOL (crypto altcoin)
    sol_df = make_candle_df(100, trend_slope=0.002, vol=0.005)
    manager.initialize_pair_history("SOL/USD", "1h", sol_df)
    manager.initialize_pair_history("SOL/USD", "15m", sol_df)

    strategy = MultiRegimeAlphaStrategy()
    signals = strategy.generate_signals(manager, active_pairs=[])

    # Altcoins like SOL must be gated in CHOP
    sol_signals = [s for s in signals if s.pair == "SOL/USD"]
    assert len(sol_signals) == 0

    # US Equity METAB must be allowed through
    meta_signals = [s for s in signals if s.pair == "METAB/USD"]
    assert len(meta_signals) > 0
    assert meta_signals[0].action == SignalAction.BUY

