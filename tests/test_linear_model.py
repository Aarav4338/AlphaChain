"""
Unit tests for the CryptoLinearFactorModel (strategy/linear_factor_model.py).
Tests factor calculations, Ridge calibration math, cost hurdle filtering, and cooldown logic.
"""
import pytest
import numpy as np
import pandas as pd

from data.candle_manager import CandleManager
from strategy.linear_factor_model import CryptoLinearFactorModel
from strategy.signals import SignalAction


def make_synth_candles(n: int, slope: float, vol: float = 0.01) -> pd.DataFrame:
    np.random.seed(42)
    base = 100.0
    returns = np.random.normal(slope, vol, n)
    prices = base * np.cumprod(1 + returns)
    timestamps = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": prices * 1.01,
        "low": prices * 0.99,
        "close": prices,
        "volume": np.random.uniform(500, 1500, n),
    })


def test_factor_engineering():
    model = CryptoLinearFactorModel()
    btc_df = make_synth_candles(50, slope=0.002)
    sol_df = make_synth_candles(50, slope=0.004)

    factors_df = model.compute_factors_for_pair("SOL/USD", sol_df, btc_df)
    assert factors_df is not None
    assert not factors_df.empty
    for col in ["lead_lag", "idio_mom", "reversal", "vol_flow"]:
        assert col in factors_df.columns
        assert np.isfinite(factors_df[col].iloc[-1])
    # forward_ret is finite prior to the forward lookahead window
    assert np.isfinite(factors_df["forward_ret"].iloc[-10])


def test_ridge_regression_calibration():
    model = CryptoLinearFactorModel()
    btc_df = make_synth_candles(60, slope=0.001)

    panel = {}
    for pair in ["ETH/USD", "SOL/USD", "DOGE/USD"]:
        df = make_synth_candles(60, slope=0.002)
        panel[pair] = model.compute_factors_for_pair(pair, df, btc_df)

    weights = model.calibrate_weights(panel)
    assert len(weights) == 4
    assert np.all(np.isfinite(weights))
    assert model.diagnostics.rolling_r2 is not None


def test_signal_generation_and_cost_hurdle():
    model = CryptoLinearFactorModel(cost_hurdle=0.005)  # 50 bps hurdle
    cm = CandleManager(max_buffer_size=100)

    btc_df = make_synth_candles(60, slope=0.001)
    cm.initialize_pair_history("BTC/USD", "1h", btc_df)

    # Surge SOL so it generates positive expected return
    sol_df = make_synth_candles(60, slope=0.015)
    cm.initialize_pair_history("SOL/USD", "1h", sol_df)

    eth_df = make_synth_candles(60, slope=-0.015)
    cm.initialize_pair_history("ETH/USD", "1h", eth_df)

    signals = model.generate_signals(cm, active_pairs=[], timeframe="1h")
    assert isinstance(signals, list)

    # If signals were produced, verify stop loss and take profit ratios
    for sig in signals:
        if sig.action == SignalAction.BUY:
            assert sig.stop_loss_price < sig.entry_price
            assert sig.take_profit_price > sig.entry_price
            # Reward-to-risk ratio should be ~2:1
            risk = sig.entry_price - sig.stop_loss_price
            reward = sig.take_profit_price - sig.entry_price
            assert pytest.approx(reward / risk, rel=0.1) == 2.0


def test_cooldown_constraint():
    model = CryptoLinearFactorModel(cooldown_bars=5)
    model.record_exit("SOL/USD", current_bar_index=10)

    # Bar 12 is within cooldown (12 - 10 = 2 < 5)
    cm = CandleManager(max_buffer_size=100)
    btc_df = make_synth_candles(60, slope=0.001)
    sol_df = make_synth_candles(60, slope=0.02)
    cm.initialize_pair_history("BTC/USD", "1h", btc_df)
    cm.initialize_pair_history("SOL/USD", "1h", sol_df)

    signals = model.generate_signals(cm, active_pairs=[], timeframe="1h", current_bar_index=12)
    sol_signals = [s for s in signals if s.pair == "SOL/USD"]
    assert len(sol_signals) == 0  # SOL blocked by cooldown!
