from dataclasses import dataclass, field
import logging
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

from config.trading_config import trading_config
from data.candle_manager import CandleManager
from strategy.signals import TradeSignal, SignalAction

logger = logging.getLogger("AlphaChain.LinearModel")


@dataclass
class LinearModelDiagnostics:
    rolling_r2: float = 0.0
    information_coefficient: float = 0.0
    factor_weights: Dict[str, float] = field(default_factory=dict)
    factor_t_stats: Dict[str, float] = field(default_factory=dict)
    active_predictions: Dict[str, float] = field(default_factory=dict)


class CryptoLinearFactorModel:
    FACTOR_NAMES = ["lead_lag", "idio_mom", "reversal", "vol_flow"]

    def __init__(
        self,
        benchmark_pair: str = "BTC/USD",
        calibration_window: int = 72,
        prediction_horizon: int = 4,
        ridge_lambda: float = 1.0,
        cost_hurdle: float = 0.0020,
        max_positions: int = 3,
        cooldown_bars: int = 16,
    ):
        self.benchmark_pair = benchmark_pair
        self.calibration_window = calibration_window
        self.prediction_horizon = prediction_horizon
        self.ridge_lambda = ridge_lambda
        self.cost_hurdle = cost_hurdle
        self.max_positions = max_positions
        self.cooldown_bars = cooldown_bars

        self.last_exit_times: Dict[str, int] = {}
        self.consecutive_losses: Dict[str, int] = {}
        self.diagnostics = LinearModelDiagnostics()
        
        # Load calibrated factor weights from profile if available
        profile_file = Path(__file__).resolve().parent.parent / "config" / "calibrated_optimal_profile.json"
        if profile_file.exists():
            try:
                with open(profile_file, "r", encoding="utf-8") as f:
                    prof_data = json.load(f)
                fw = prof_data.get("factor_weights", {})
                self.cached_weights = np.array([
                    fw.get("lead_lag", 0.3311),
                    fw.get("idio_mom", 0.4580),
                    fw.get("reversal", 0.3036),
                    fw.get("vol_flow", -0.6562),
                ])
                if "cost_hurdle" in prof_data:
                    self.cost_hurdle = float(prof_data["cost_hurdle"])
            except Exception:
                self.cached_weights = np.array([0.3311, 0.4580, 0.3036, -0.6562])
        else:
            self.cached_weights = np.array([0.3311, 0.4580, 0.3036, -0.6562])

    def compute_factors_for_pair(
        self,
        pair: str,
        df: pd.DataFrame,
        btc_df: pd.DataFrame,
    ) -> Optional[pd.DataFrame]:
        if len(df) < 30 or len(btc_df) < 30:
            return None

        merged = pd.merge(
            df[["timestamp", "close", "volume"]],
            btc_df[["timestamp", "close"]],
            on="timestamp",
            suffixes=("", "_btc"),
        ).sort_values("timestamp").reset_index(drop=True)

        if len(merged) < 25:
            return None

        close = merged["close"].values
        close_btc = merged["close_btc"].values
        volume = merged["volume"].values

        r_asset = np.diff(np.log(close), prepend=np.nan)
        r_btc = np.diff(np.log(close_btc), prepend=np.nan)
        n = len(merged)
        factors = pd.DataFrame(index=range(n))

        if pair == self.benchmark_pair:
            lead_lag = pd.Series(r_btc).rolling(6, min_periods=2).sum().values
            idio_mom = pd.Series(r_btc).rolling(12, min_periods=4).sum().values
        else:
            beta_window = 24
            betas = np.ones(n)
            for i in range(beta_window, n):
                sub_a = r_asset[i - beta_window : i]
                sub_b = r_btc[i - beta_window : i]
                var_b = np.var(sub_b)
                if var_b > 1e-8:
                    betas[i] = np.clip(np.cov(sub_a, sub_b)[0, 1] / var_b, 0.2, 3.0)

            lead_lag = (betas * r_btc) - r_asset
            idio_ret = r_asset - (betas * r_btc)
            idio_mom = pd.Series(idio_ret).rolling(12, min_periods=4).sum().values

        reversal = -r_asset

        vol_s = pd.Series(volume)
        vol_mean = vol_s.rolling(20, min_periods=5).mean().values
        vol_std = vol_s.rolling(20, min_periods=5).std().values
        vol_z = np.where(vol_std > 1e-8, (volume - vol_mean) / vol_std, 0.0)
        vol_flow = vol_z * np.sign(r_asset)

        h = self.prediction_horizon
        fwd_ret = np.full(n, np.nan)
        for i in range(n - h):
            fwd_ret[i] = np.log(close[i + h] / close[i])

        factors["lead_lag"] = lead_lag
        factors["idio_mom"] = idio_mom
        factors["reversal"] = reversal
        factors["vol_flow"] = vol_flow
        factors["forward_ret"] = fwd_ret
        factors["timestamp"] = merged["timestamp"]

        return factors.dropna(subset=["lead_lag", "idio_mom", "reversal", "vol_flow"]).reset_index(drop=True)

    def calibrate_weights(self, panel_data: Dict[str, pd.DataFrame]) -> np.ndarray:
        all_X = []
        all_y = []

        for pair, df in panel_data.items():
            if df.empty or "forward_ret" not in df:
                continue
            valid = df.dropna(subset=["forward_ret"])
            if len(valid) < 20:
                continue

            sub = valid.iloc[-self.calibration_window :]
            all_X.append(sub[self.FACTOR_NAMES].values)
            all_y.append(sub["forward_ret"].values)

        if not all_X or len(all_X) < 2:
            return self.cached_weights

        X = np.vstack(all_X)
        y = np.concatenate(all_y)

        mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
        X = X[mask]
        y = y[mask]

        if len(y) < 50:
            return self.cached_weights

        X_mean = np.mean(X, axis=0)
        X_std = np.std(X, axis=0) + 1e-8
        X_norm = (X - X_mean) / X_std

        k = X_norm.shape[1]
        XtX = X_norm.T @ X_norm
        reg_matrix = XtX + (self.ridge_lambda * np.eye(k))

        try:
            beta_hat = np.linalg.solve(reg_matrix, X_norm.T @ y)

            y_pred = X_norm @ beta_hat
            residuals = y - y_pred
            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1.0 - (ss_res / (ss_tot + 1e-8))

            mse = ss_res / max(1, len(y) - k)
            cov_beta = mse * np.linalg.pinv(reg_matrix)
            se_beta = np.sqrt(np.diag(cov_beta))
            t_stats = beta_hat / (se_beta + 1e-8)

            rank_pred = pd.Series(y_pred).rank().values
            rank_actual = pd.Series(y).rank().values
            ic = float(np.corrcoef(rank_pred, rank_actual)[0, 1])

            self.diagnostics.rolling_r2 = float(r2)
            self.diagnostics.information_coefficient = ic if np.isfinite(ic) else 0.0
            self.diagnostics.factor_weights = {name: float(beta_hat[i]) for i, name in enumerate(self.FACTOR_NAMES)}
            self.diagnostics.factor_t_stats = {name: float(t_stats[i]) for i, name in enumerate(self.FACTOR_NAMES)}

            self.cached_weights = (0.7 * self.cached_weights) + (0.3 * beta_hat)
        except Exception as e:
            logger.warning(f"Ridge calibration fallback: {e}")

        return self.cached_weights

    def compute_btc_velocity_score(self, btc_candles: pd.DataFrame) -> Tuple[float, float]:
        """
        Compute ATR-normalized trend velocity (v_BTC) and multi-horizon momentum vector (m_BTC).
        v_BTC = (EMA_12 - EMA_26) / ATR_14
        m_BTC = 0.50 * (r_4h / sigma_24h) + 0.30 * (r_12h / (sigma_24h * sqrt(3))) + 0.20 * (r_24h / (sigma_24h * sqrt(6)))
        """
        if len(btc_candles) < 15:
            return 0.0, 0.0

        close = btc_candles["close"].values
        n = len(close)

        close_series = pd.Series(close)
        ema_12 = float(close_series.ewm(span=12, adjust=False).mean().iloc[-1])
        ema_26 = float(close_series.ewm(span=26, adjust=False).mean().iloc[-1])

        if "high" in btc_candles.columns and "low" in btc_candles.columns:
            high = btc_candles["high"].values
            low = btc_candles["low"].values
            tr = np.maximum(high - low, np.abs(high - np.roll(close, 1)))
            tr[0] = high[0] - low[0]
            atr_14 = float(pd.Series(tr).rolling(14, min_periods=5).mean().iloc[-1])
        else:
            atr_14 = float(close[-1] * 0.02)

        atr_14 = max(atr_14, close[-1] * 0.001, 1e-6)
        v_btc = (ema_12 - ema_26) / atr_14

        log_ret = np.diff(np.log(close))
        if len(log_ret) >= 4:
            sigma_window = min(24, len(log_ret))
            sigma_24h = float(np.std(log_ret[-sigma_window:]))
        else:
            sigma_24h = 0.01

        sigma_24h = max(sigma_24h, 1e-5)

        curr_p = close[-1]
        p_4 = close[max(0, n - 5)]
        p_12 = close[max(0, n - 13)]
        p_24 = close[max(0, n - 25)]

        r_4h = float(np.log(curr_p / p_4)) if p_4 > 0 else 0.0
        r_12h = float(np.log(curr_p / p_12)) if p_12 > 0 else 0.0
        r_24h = float(np.log(curr_p / p_24)) if p_24 > 0 else 0.0

        m_btc = (
            0.50 * (r_4h / sigma_24h)
            + 0.30 * (r_12h / (sigma_24h * np.sqrt(3.0)))
            + 0.20 * (r_24h / (sigma_24h * np.sqrt(6.0)))
        )

        return float(v_btc), float(m_btc)

    def compute_btc_signal(
        self,
        btc_candles: pd.DataFrame,
        current_bar_index: int = 0,
    ) -> Optional[TradeSignal]:
        """
        Generate standalone TradeSignal for BTC/USD based on dedicated trend velocity & momentum.
        Avoids distortion from cross-sectional altcoin spread standardizations.
        """
        if len(btc_candles) < 20:
            return None

        last_exit = self.last_exit_times.get(self.benchmark_pair, -999)
        loss_count = self.consecutive_losses.get(self.benchmark_pair, 0)
        backoff_mult = min(4, 2 ** loss_count) if loss_count > 0 else 1
        effective_cooldown = self.cooldown_bars * backoff_mult

        if current_bar_index - last_exit < effective_cooldown:
            return None

        v_btc, m_btc = self.compute_btc_velocity_score(btc_candles)
        curr_p = float(btc_candles["close"].iloc[-1])

        ema_50 = float(btc_candles["close"].ewm(span=50, adjust=False).mean().iloc[-1]) if len(btc_candles) >= 30 else curr_p

        if "high" in btc_candles.columns and "low" in btc_candles.columns:
            high_low = btc_candles["high"] - btc_candles["low"]
            atr_val = float(high_low.rolling(14, min_periods=5).mean().iloc[-1])
        else:
            atr_val = float(curr_p * 0.02)
        atr_val = max(atr_val, curr_p * 0.005)

        stop_dist = max(atr_val * 1.5, curr_p * 0.015)
        target_dist = stop_dist * 2.0

        if v_btc > 0.0 and m_btc > 0.20 and curr_p > ema_50:
            conviction = float(np.clip((v_btc + m_btc) / 2.0, 0.20, 1.0))
            return TradeSignal(
                pair=self.benchmark_pair,
                action=SignalAction.BUY,
                conviction=conviction,
                entry_price=curr_p,
                stop_loss_price=curr_p - stop_dist,
                take_profit_price=curr_p + target_dist,
                atr=atr_val,
                reason=f"BTC Velocity Alpha: v={v_btc:.2f}, m={m_btc:.2f}, conv={conviction:.2f}",
            )

        return None

    def generate_signals(
        self,
        candle_manager: CandleManager,
        active_pairs: Optional[List[str]] = None,
        timeframe: str = "1h",
        current_bar_index: int = 0,
        regime: Optional[Any] = None,
    ) -> List[TradeSignal]:
        active_set = set(active_pairs or [])
        universe = trading_config.ASSET_UNIVERSE
        btc_df = candle_manager.get_candles(self.benchmark_pair, timeframe=timeframe)

        if btc_df is None or len(btc_df) < 30:
            return []

        signals: List[TradeSignal] = []

        # 1. Dedicated BTC Velocity Scoring (isolated from altcoin cross-sectional distortion)
        if self.benchmark_pair not in active_set:
            btc_signal = self.compute_btc_signal(btc_df, current_bar_index=current_bar_index)
            if btc_signal is not None:
                signals.append(btc_signal)

        # 2. Altcoin Cross-Sectional Panel Processing
        panel: Dict[str, pd.DataFrame] = {}
        latest_features: Dict[str, np.ndarray] = {}
        latest_prices: Dict[str, float] = {}

        for pair in universe:
            if pair == self.benchmark_pair:
                continue
            df = candle_manager.get_candles(pair, timeframe=timeframe)
            if df is None or len(df) < 30:
                continue

            factors_df = self.compute_factors_for_pair(pair, df, btc_df)
            if factors_df is not None and not factors_df.empty:
                panel[pair] = factors_df
                latest_features[pair] = factors_df[self.FACTOR_NAMES].iloc[-1].values
                latest_prices[pair] = float(df["close"].iloc[-1])

        if len(panel) >= 2:
            weights = self.calibrate_weights(panel)

            raw_mat = np.array([latest_features[p] for p in panel.keys()])
            pair_list = list(panel.keys())

            cs_mean = np.mean(raw_mat, axis=0)
            cs_std = np.std(raw_mat, axis=0) + 1e-8
            cs_norm = (raw_mat - cs_mean) / cs_std

            expected_returns = cs_norm @ weights
            self.diagnostics.active_predictions = {pair_list[i]: float(expected_returns[i]) for i in range(len(pair_list))}

            for i, pair in enumerate(pair_list):
                if pair in active_set:
                    continue

                last_exit = self.last_exit_times.get(pair, -999)
                loss_count = self.consecutive_losses.get(pair, 0)
                backoff_mult = min(4, 2 ** loss_count) if loss_count > 0 else 1
                effective_cooldown = self.cooldown_bars * backoff_mult

                if current_bar_index - last_exit < effective_cooldown:
                    continue

                mu_pred = expected_returns[i]
                curr_p = candle_manager.get_latest_price(pair) or latest_prices[pair]

                df_pair = candle_manager.get_candles(pair, timeframe=timeframe)
                atr_val = float(df_pair["close"].iloc[-1] * 0.025)
                if len(df_pair) >= 15:
                    high_low = df_pair["high"] - df_pair["low"]
                    atr_val = float(high_low.rolling(14).mean().iloc[-1])

                ema_50 = float(df_pair["close"].ewm(span=50, adjust=False).mean().iloc[-1]) if len(df_pair) >= 20 else curr_p

                stop_dist = max(atr_val * 1.5, curr_p * 0.015)
                target_dist = stop_dist * 2.0

                if mu_pred > self.cost_hurdle:
                    if (regime == "BEAR" or getattr(regime, "name", "") == "BEAR") and curr_p < ema_50:
                        continue
                    signals.append(
                        TradeSignal(
                            pair=pair,
                            action=SignalAction.BUY,
                            conviction=float(min(1.0, mu_pred / (self.cost_hurdle * 3.0))),
                            entry_price=curr_p,
                            stop_loss_price=curr_p - stop_dist,
                            take_profit_price=curr_p + target_dist,
                            atr=atr_val,
                            reason=f"Linear Model LONG: E[R]={mu_pred * 100:+.2f}%",
                        )
                    )
                elif mu_pred < -self.cost_hurdle:
                    if (regime == "BULL" or getattr(regime, "name", "") == "BULL") and curr_p > ema_50:
                        continue
                    signals.append(
                        TradeSignal(
                            pair=pair,
                            action=SignalAction.SHORT_OPEN,
                            conviction=float(min(1.0, abs(mu_pred) / (self.cost_hurdle * 3.0))),
                            entry_price=curr_p,
                            stop_loss_price=curr_p + stop_dist,
                            take_profit_price=curr_p - target_dist,
                            atr=atr_val,
                            reason=f"Linear Model SHORT: E[R]={mu_pred * 100:+.2f}%",
                        )
                    )

        signals.sort(key=lambda s: s.conviction, reverse=True)
        return signals

    def record_exit(self, pair: str, current_bar_index: int, net_pnl: float = 0.0) -> None:
        self.last_exit_times[pair] = current_bar_index
        if net_pnl < 0:
            self.consecutive_losses[pair] = self.consecutive_losses.get(pair, 0) + 1
        elif net_pnl > 0:
            self.consecutive_losses[pair] = 0
            # For winning trades, reduce cooldown to 2 bars (30m) instead of 16 bars (4 hours)
            # so the model can re-enter high-conviction macro continuations without idle lockouts
            self.last_exit_times[pair] = current_bar_index - max(0, self.cooldown_bars - 2)
