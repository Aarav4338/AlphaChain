"""
Unit tests for BinanceFetcher, CandleManager, and quantitative indicators.
"""
import pytest
import pandas as pd
import numpy as np

from client.binance_fetcher import BinanceFetcher
from data.candle_manager import CandleManager
from data.indicators import (
    compute_ema,
    compute_atr,
    compute_rsi,
    compute_adx,
    compute_bollinger_bands,
    compute_relative_strength_ranks,
    compute_volume_surge_ratio,
    compute_volatility_squeeze,
    compute_zscore,
)


@pytest.fixture
def sample_kline_df():
    """Generates a synthetic 50-candle OHLCV DataFrame for deterministic testing."""
    np.random.seed(42)
    n = 60
    base = 100.0
    returns = np.random.normal(0.001, 0.01, n)
    prices = base * np.cumprod(1 + returns)

    timestamps = pd.date_range("2026-10-01 00:00:00", periods=n, freq="15min", tz="UTC")
    highs = prices * (1 + np.abs(np.random.normal(0.002, 0.002, n)))
    lows = prices * (1 - np.abs(np.random.normal(0.002, 0.002, n)))
    opens = prices * (1 + np.random.normal(0.0, 0.001, n))
    volumes = np.random.uniform(10, 100, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    })


def test_binance_fetcher():
    """Verifies that the Binance public REST API returns valid formatted candles."""
    fetcher = BinanceFetcher()
    df = fetcher.fetch_klines("BTC/USD", interval="15m", limit=10)
    assert df is not None, "Binance fetcher returned None"
    assert len(df) == 10
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert (df["high"] >= df["low"]).all()
    assert (df["close"] > 0).all()


def test_candle_manager(sample_kline_df):
    """Verifies CandleManager historical seeding and live tick updates."""
    manager = CandleManager(max_buffer_size=100)
    pair = "BTC/USD"
    manager.initialize_pair_history(pair, "15m", sample_kline_df)

    candles = manager.get_candles(pair, "15m")
    assert candles is not None
    assert len(candles) == len(sample_kline_df)
    assert manager.get_latest_price(pair) == sample_kline_df.iloc[-1]["close"]

    # Simulate tick within current candle
    current_time = sample_kline_df.iloc[-1]["timestamp"]
    manager.update_tick(pair, last_price=99999.0, max_bid=99998.0, min_ask=99999.0, timestamp=current_time)
    assert manager.get_latest_price(pair) == 99999.0
    updated_candles = manager.get_candles(pair, "15m")
    assert updated_candles.iloc[-1]["high"] >= 99999.0

    # Simulate tick in new candle (20 minutes later)
    new_time = current_time + pd.Timedelta(minutes=20)
    manager.update_tick(pair, last_price=88888.0, max_bid=88887.0, min_ask=88888.0, timestamp=new_time)
    candles_after_new_period = manager.get_candles(pair, "15m")
    assert len(candles_after_new_period) == len(sample_kline_df) + 1
    assert candles_after_new_period.iloc[-1]["open"] == 88888.0


def test_indicators_math(sample_kline_df):
    """Verifies correctness of mathematical indicator computations."""
    df = sample_kline_df

    # 1. EMA
    ema20 = compute_ema(df["close"], period=20)
    assert len(ema20) == len(df)
    assert not ema20.isna().any()

    # 2. ATR
    atr = compute_atr(df, period=14)
    assert len(atr) == len(df)
    assert (atr.dropna() > 0).all()

    # 3. RSI
    rsi = compute_rsi(df["close"], period=14)
    assert len(rsi) == len(df)
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0.0).all() and (valid_rsi <= 100.0).all()

    # 4. ADX
    adx_df = compute_adx(df, period=14)
    assert set(adx_df.columns) == {"adx", "plus_di", "minus_di"}
    valid_adx = adx_df["adx"].dropna()
    assert (valid_adx >= 0.0).all() and (valid_adx <= 100.0).all()

    # 5. Bollinger Bands
    bb = compute_bollinger_bands(df["close"], window=20, num_std=2.0)
    valid_bb = bb.dropna()
    assert (valid_bb["bb_upper"] >= valid_bb["bb_middle"]).all()
    assert (valid_bb["bb_middle"] >= valid_bb["bb_lower"]).all()


def test_relative_strength_ranks():
    """Verifies that cross-sectional relative strength correctly identifies leaders and laggards."""
    # Create price histories where SOL pumps 20%, BTC pumps 5%, and ETH drops 5%
    n = 30
    dates = pd.date_range("2026-10-01", periods=n, freq="1h")

    btc_prices = pd.Series(np.linspace(100, 105, n), index=dates)  # +5%
    sol_prices = pd.Series(np.linspace(50, 60, n), index=dates)    # +20% (Leader)
    eth_prices = pd.Series(np.linspace(200, 190, n), index=dates)  # -5% (Laggard)

    close_dict = {
        "BTC/USD": btc_prices,
        "SOL/USD": sol_prices,
        "ETH/USD": eth_prices,
    }

    ranks = compute_relative_strength_ranks(close_dict, benchmark_pair="BTC/USD", lookback=24)
    pairs_ranked = list(ranks.keys())

    # SOL should be ranked 1st (highest relative strength)
    assert pairs_ranked[0] == "SOL/USD"
    assert ranks["SOL/USD"] > 0

    # ETH should be ranked last (weakest relative strength)
    assert pairs_ranked[-1] == "ETH/USD"
    assert ranks["ETH/USD"] < 0


def test_new_dimensions_math(sample_kline_df):
    """Verifies correctness of Volume Surge, Volatility Squeeze, and Z-Score."""
    df = sample_kline_df

    # 1. Volume Surge Ratio
    vol_surge = compute_volume_surge_ratio(df, window=20)
    assert len(vol_surge) == len(df)
    assert not vol_surge.isna().any()
    assert (vol_surge > 0).all()

    # 2. Volatility Squeeze
    squeeze_df = compute_volatility_squeeze(df, bb_window=20, kc_window=20)
    assert set(squeeze_df.columns) == {
        "is_squeeze",
        "bb_upper",
        "bb_lower",
        "kc_upper",
        "kc_lower",
        "squeeze_momentum",
    }
    assert isinstance(squeeze_df["is_squeeze"].iloc[-1], (bool, np.bool_))
    assert not squeeze_df["squeeze_momentum"].isna().any()

    # 3. Z-Score
    zscore = compute_zscore(df["close"], window=20)
    assert len(zscore) == len(df)
    assert not zscore.isna().any()
    # Z-scores of normal random data should predominantly lie within [-4, 4]
    valid_z = zscore.iloc[20:]
    assert (valid_z >= -5.0).all() and (valid_z <= 5.0).all()

