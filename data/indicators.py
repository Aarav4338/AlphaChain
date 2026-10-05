from typing import Dict
import pandas as pd
import numpy as np


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(100.0)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high = df["high"]
    low = df["low"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = df["close"].shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    smoothed_tr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smoothed_plus_dm = pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    smoothed_minus_dm = pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    plus_di = 100.0 * (smoothed_plus_dm / smoothed_tr.replace(0, np.nan))
    minus_di = 100.0 * (smoothed_minus_dm / smoothed_tr.replace(0, np.nan))

    di_diff = (plus_di - minus_di).abs()
    di_sum = plus_di + minus_di
    dx = 100.0 * (di_diff / di_sum.replace(0, np.nan)).fillna(0.0)
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return pd.DataFrame({
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }, index=df.index)


def compute_bollinger_bands(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    middle = series.rolling(window=window).mean()
    std = series.rolling(window=window).std(ddof=0)
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    bandwidth = upper - lower
    percent_b = (series - lower) / bandwidth.replace(0, np.nan)

    return pd.DataFrame({
        "bb_middle": middle,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_percent_b": percent_b,
    }, index=series.index)


def compute_relative_strength_ranks(
    close_dict: Dict[str, pd.Series],
    benchmark_pair: str = "BTC/USD",
    lookback: int = 24,
) -> Dict[str, float]:
    if benchmark_pair not in close_dict:
        return {}

    bench_series = close_dict[benchmark_pair]
    if len(bench_series) <= lookback:
        return {}

    bench_return = (bench_series.iloc[-1] / bench_series.iloc[-1 - lookback]) - 1.0
    rs_scores: Dict[str, float] = {}

    for pair, s in close_dict.items():
        if len(s) > lookback and s.iloc[-1 - lookback] > 0:
            asset_return = (s.iloc[-1] / s.iloc[-1 - lookback]) - 1.0
            rs_scores[pair] = asset_return - bench_return

    return dict(sorted(rs_scores.items(), key=lambda x: x[1], reverse=True))


def compute_volume_surge_ratio(df: pd.DataFrame, window: int = 20) -> pd.Series:
    volume = df["volume"]
    rolling_vol_mean = volume.rolling(window=window, min_periods=max(5, window // 2)).mean()
    surge_ratio = volume / rolling_vol_mean.replace(0, np.nan)
    return surge_ratio.fillna(1.0)


def compute_volatility_squeeze(
    df: pd.DataFrame,
    bb_window: int = 20,
    bb_std: float = 2.0,
    kc_window: int = 20,
    kc_mult: float = 1.5,
) -> pd.DataFrame:
    close = df["close"]
    high = df["high"]
    low = df["low"]

    bb_basis = close.rolling(window=bb_window, min_periods=1).mean()
    bb_dev = close.rolling(window=bb_window, min_periods=1).std(ddof=0).fillna(0.0)
    bb_upper = bb_basis + (bb_std * bb_dev)
    bb_lower = bb_basis - (bb_std * bb_dev)

    kc_basis = close.ewm(span=kc_window, adjust=False).mean()
    prev_close = close.shift(1).fillna(close.iloc[0])
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    kc_range = tr.ewm(span=kc_window, adjust=False).mean()
    kc_upper = kc_basis + (kc_mult * kc_range)
    kc_lower = kc_basis - (kc_mult * kc_range)

    is_squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    midline = (bb_basis + kc_basis) / 2.0
    squeeze_momentum = (close - midline).fillna(0.0)

    return pd.DataFrame({
        "is_squeeze": is_squeeze,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "kc_upper": kc_upper,
        "kc_lower": kc_lower,
        "squeeze_momentum": squeeze_momentum,
    }, index=df.index)


def compute_zscore(series: pd.Series, window: int = 20) -> pd.Series:
    mean = series.rolling(window=window, min_periods=1).mean()
    std = series.rolling(window=window, min_periods=1).std(ddof=0)
    z_score = (series - mean) / std.replace(0, np.nan)
    return z_score.fillna(0.0)
