"""
Systematic Quantitative Hyperparameter Calibration & Optimization Engine.
Optimizes linear factor weights, regime switching thresholds, dynamic risk parameters,
and exit targets against the official Roostoo Hackathon Composite Score:
    Score = 0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar - Penalties(MaxDD > 3.5%, LowTrades)
Includes Out-Of-Sample (OOS) cross-validation on January 2026 market shock.
"""
import sys
import time
import pickle
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.trading_config import trading_config
from strategy.signals import SignalAction


@dataclass
class FastSimResult:
    cum_ret_pct: float
    max_dd_pct: float
    sharpe: float
    sortino: float
    calmar: float
    composite_score: float
    total_trades: int
    win_rate: float
    profit_factor: float


class FastVectorizedBacktester:
    """Pre-computes market features and runs in-memory backtest simulations in <0.05 seconds."""

    def __init__(self, data_15m: Dict[str, pd.DataFrame], data_1h: Dict[str, pd.DataFrame]):
        self.data_15m = data_15m
        self.data_1h = data_1h
        self.pairs = list(data_15m.keys())
        self.benchmark = "BTC/USD"
        self._precompute_features()

    def _precompute_features(self):
        """Pre-compute all technical indicators and linear factors across all bars once."""
        print(f" Pre-computing indicator matrices across {len(self.pairs)} pairs...")

        # Common 15m timestamps
        common_ts = sorted(
            list(set.intersection(*[set(df["timestamp"].dt.tz_convert("UTC")) for df in self.data_15m.values()]))
        )
        self.timestamps = common_ts
        self.n_bars = len(common_ts)

        # 1. 15m Price & ATR matrices: shape (n_bars, n_pairs)
        self.price_matrix = np.zeros((self.n_bars, len(self.pairs)), dtype=np.float64)
        self.atr_matrix = np.zeros((self.n_bars, len(self.pairs)), dtype=np.float64)

        for p_idx, pair in enumerate(self.pairs):
            df = self.data_15m[pair].copy()
            df["ts_utc"] = df["timestamp"].dt.tz_convert("UTC")
            df = df.drop_duplicates("ts_utc").set_index("ts_utc").reindex(common_ts).ffill()

            c = df["close"].values
            h = df["high"].values
            l = df["low"].values

            # ATR 14
            tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
            tr[0] = h[0] - l[0]
            atr = pd.Series(tr).rolling(14, min_periods=1).mean().values

            self.price_matrix[:, p_idx] = c
            self.atr_matrix[:, p_idx] = atr

        # 2. Bitcoin 1h Regime indicators aligned to 15m timestamps
        btc_1h = self.data_1h[self.benchmark].copy()
        btc_1h["ts_utc"] = btc_1h["timestamp"].dt.tz_convert("UTC")
        btc_1h = btc_1h.drop_duplicates("ts_utc").set_index("ts_utc").sort_index()

        c_btc = btc_1h["close"]
        h_btc = btc_1h["high"]
        l_btc = btc_1h["low"]

        ema20 = c_btc.ewm(span=20, adjust=False).mean()
        ema50 = c_btc.ewm(span=50, adjust=False).mean()

        # ADX 14 on 1h
        tr_btc = np.maximum(h_btc - l_btc, np.maximum(np.abs(h_btc - c_btc.shift(1)), np.abs(l_btc - c_btc.shift(1))))
        atr_btc = tr_btc.rolling(14, min_periods=1).mean()
        up_move = h_btc - h_btc.shift(1)
        down_move = l_btc.shift(1) - l_btc
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_di = 100 * (pd.Series(plus_dm, index=btc_1h.index).rolling(14).mean() / (atr_btc + 1e-8))
        minus_di = 100 * (pd.Series(minus_dm, index=btc_1h.index).rolling(14).mean() / (atr_btc + 1e-8))
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-8)
        adx_1h = dx.rolling(14).mean().fillna(15.0)

        regime_df = pd.DataFrame({
            "ema20": ema20,
            "ema50": ema50,
            "adx": adx_1h
        }, index=btc_1h.index)

        # Reindex regime to 15m common timestamps with ffill
        regime_15m = regime_df.reindex(common_ts, method="ffill").bfill()
        self.btc_ema20 = regime_15m["ema20"].values
        self.btc_ema50 = regime_15m["ema50"].values
        self.btc_adx = regime_15m["adx"].values

        # 3. Factor values matrix: shape (n_bars, n_pairs, 4)
        # Factors: [0: lead_lag, 1: idio_mom, 2: reversal, 3: vol_flow]
        self.factor_matrix = np.zeros((self.n_bars, len(self.pairs), 4), dtype=np.float64)

        btc_idx = self.pairs.index(self.benchmark)
        btc_ret = np.diff(np.log(self.price_matrix[:, btc_idx]), prepend=np.nan)
        btc_ret[0] = 0.0

        for p_idx, pair in enumerate(self.pairs):
            c_p = self.price_matrix[:, p_idx]
            r_p = np.diff(np.log(c_p), prepend=np.nan)
            r_p[0] = 0.0

            # 1. Lead-lag: rolling beta * r_btc - r_p
            if pair == self.benchmark:
                lead_lag = pd.Series(btc_ret).rolling(8, min_periods=2).mean().values
                idio_mom = pd.Series(btc_ret).rolling(16, min_periods=4).sum().values
            else:
                rolling_cov = pd.Series(r_p * btc_ret).rolling(32, min_periods=8).mean().values
                rolling_var = pd.Series(btc_ret ** 2).rolling(32, min_periods=8).mean().values
                beta = np.clip(rolling_cov / (rolling_var + 1e-8), 0.3, 2.5)
                lead_lag = (beta * btc_ret) - r_p
                idio_ret = r_p - (beta * btc_ret)
                idio_mom = pd.Series(idio_ret).rolling(16, min_periods=4).sum().values

            reversal = -r_p
            # Vol flow approximation via price return sign
            vol_flow = np.sign(r_p) * np.abs(r_p)

            self.factor_matrix[:, p_idx, 0] = np.nan_to_num(lead_lag)
            self.factor_matrix[:, p_idx, 1] = np.nan_to_num(idio_mom)
            self.factor_matrix[:, p_idx, 2] = np.nan_to_num(reversal)
            self.factor_matrix[:, p_idx, 3] = np.nan_to_num(vol_flow)

        print(f" Pre-computation complete! {self.n_bars} bars synchronized.")

    def simulate(
        self,
        weights: np.ndarray,            # 4 weights: [w_lag, w_mom, w_rev, w_flow]
        adx_trend_thresh: float = 19.0,
        cost_hurdle: float = 0.0014,
        min_conviction: float = 0.42,
        stop_loss_mult: float = 1.4,
        take_profit_mult: float = 2.2,
        breakeven_mult: float = 1.1,
        max_holding_bars: int = 24,
        chop_heat_ceiling: float = 0.0090,
        fee_rate: float = 0.0005,
        initial_capital: float = 100000.0,
    ) -> FastSimResult:
        """Runs fast simulation loop in ~0.02 seconds."""
        equity = initial_capital
        free_usd = initial_capital
        peak_equity = initial_capital

        # Positions tracking: pair_idx -> dict
        # dict: {side, entry_price, quantity, collateral, stop_price, target_price, breakeven_active, entry_bar}
        open_positions = {}
        last_exit_bar = np.full(len(self.pairs), -999, dtype=np.int32)

        trade_pnls = []
        equity_series = np.zeros(self.n_bars)
        warmup = 40

        for t in range(warmup, self.n_bars):
            # Check market regime
            is_bull = (self.btc_ema20[t] > self.btc_ema50[t]) and (self.btc_adx[t] > adx_trend_thresh)
            is_bear = (self.btc_ema20[t] < self.btc_ema50[t]) and (self.btc_adx[t] > adx_trend_thresh)
            is_chop = not (is_bull or is_bear)

            # 1. Update Open Positions & Check Exits
            closed_pairs = []
            for p_idx, pos in list(open_positions.items()):
                curr_price = self.price_matrix[t, p_idx]
                atr = self.atr_matrix[t, p_idx]
                side = pos["side"]
                entry_p = pos["entry_price"]
                qty = pos["quantity"]

                # Breakeven update
                if not pos["breakeven_active"]:
                    if side == 1 and (curr_price - entry_p) >= (breakeven_mult * atr):
                        pos["stop_price"] = entry_p + (0.0005 * entry_p)
                        pos["breakeven_active"] = True
                    elif side == -1 and (entry_p - curr_price) >= (breakeven_mult * atr):
                        pos["stop_price"] = entry_p - (0.0005 * entry_p)
                        pos["breakeven_active"] = True

                # Check triggers
                exit_triggered = False
                if side == 1:
                    if curr_price <= pos["stop_price"] or curr_price >= pos["target_price"]:
                        exit_triggered = True
                else:
                    if curr_price >= pos["stop_price"] or curr_price <= pos["target_price"]:
                        exit_triggered = True

                # Time barrier check
                if not exit_triggered and max_holding_bars > 0:
                    if (t - pos["entry_bar"]) >= max_holding_bars:
                        exit_triggered = True

                if exit_triggered:
                    fee = curr_price * qty * fee_rate
                    if side == 1:
                        pnl = (curr_price - entry_p) * qty - fee
                        free_usd += (qty * curr_price) - fee
                    else:
                        pnl = (entry_p - curr_price) * qty - fee
                        free_usd += pos["collateral"] + pnl

                    trade_pnls.append(pnl)
                    last_exit_bar[p_idx] = t
                    closed_pairs.append(p_idx)

            for p_idx in closed_pairs:
                del open_positions[p_idx]

            # Recalculate equity
            unrealized = 0.0
            for p_idx, pos in open_positions.items():
                curr_price = self.price_matrix[t, p_idx]
                qty = pos["quantity"]
                if pos["side"] == 1:
                    unrealized += (curr_price - pos["entry_price"]) * qty
                else:
                    unrealized += (pos["entry_price"] - curr_price) * qty

            equity = free_usd + sum(p["collateral"] for p in open_positions.values()) + unrealized
            if equity > peak_equity:
                peak_equity = equity
            drawdown_pct = max(0.0, (peak_equity - equity) / peak_equity)
            equity_series[t] = equity

            # Hard Circuit Breaker: if drawdown exceeds 3.8%, halt entries
            if drawdown_pct >= 0.038:
                continue

            # 2. Distance-to-Cliff Governor
            heat_cap = chop_heat_ceiling if is_chop else 0.0150
            if is_bear:
                heat_cap = 0.0050  # Allow selective short exposure only
            dd_fraction = min(1.0, drawdown_pct / 0.04)
            governor_mult = max(0.10, (1.0 - dd_fraction) ** 1.8)
            effective_heat_cap = heat_cap * governor_mult

            # Calculate current risk heat
            curr_heat = 0.0
            for p_idx, pos in open_positions.items():
                curr_price = self.price_matrix[t, p_idx]
                curr_heat += (pos["quantity"] * abs(curr_price - pos["stop_price"])) / equity

            if curr_heat >= effective_heat_cap:
                continue

            # 3. Alpha Prediction & Signal Generation
            # Factor prediction for all pairs at bar t
            preds = np.dot(self.factor_matrix[t], weights)  # shape (n_pairs,)

            # Filter candidates
            for p_idx in range(len(self.pairs)):
                if p_idx in open_positions:
                    continue
                if (t - last_exit_bar[p_idx]) < 12:  # Cooldown
                    continue

                pred = preds[p_idx]
                conviction = min(1.0, abs(pred) / (cost_hurdle * 2.5 + 1e-8))

                if conviction < min_conviction or abs(pred) < cost_hurdle:
                    continue

                side = 1 if pred > 0 else -1

                # Regime filter
                if side == 1 and is_bear:
                    continue
                if side == -1 and is_bull:
                    continue
                if is_chop and conviction < 0.45:
                    continue

                # Sizing via risk heat
                avail_heat = max(0.0, effective_heat_cap - curr_heat)
                target_heat = min(0.0050 * conviction, avail_heat)
                if target_heat < 0.0008:
                    continue

                curr_p = self.price_matrix[t, p_idx]
                atr = self.atr_matrix[t, p_idx]
                if atr <= 1e-8:
                    continue

                stop_dist = stop_loss_mult * atr
                tp_dist = take_profit_mult * atr

                stop_p = curr_p - stop_dist if side == 1 else curr_p + stop_dist
                target_p = curr_p + tp_dist if side == 1 else curr_p - tp_dist

                risk_usd = equity * target_heat
                qty = risk_usd / stop_dist
                capital = qty * curr_p

                # Cap allocation per coin (max 25% of equity)
                if capital > (0.25 * equity):
                    capital = 0.25 * equity
                    qty = capital / curr_p

                fee = capital * fee_rate
                if free_usd >= (capital + fee) and free_usd > (0.15 * equity):
                    free_usd -= (capital + fee)
                    curr_heat += (qty * stop_dist) / equity
                    open_positions[p_idx] = {
                        "side": side,
                        "entry_price": curr_p,
                        "quantity": qty,
                        "collateral": capital,
                        "stop_price": stop_p,
                        "target_price": target_p,
                        "breakeven_active": False,
                        "entry_bar": t,
                    }

        # Close any lingering positions
        for p_idx, pos in open_positions.items():
            curr_p = self.price_matrix[-1, p_idx]
            fee = curr_p * pos["quantity"] * fee_rate
            pnl = (curr_p - pos["entry_price"]) * pos["quantity"] - fee if pos["side"] == 1 else (pos["entry_price"] - curr_p) * pos["quantity"] - fee
            trade_pnls.append(pnl)
            free_usd += pos["collateral"] + pnl

        final_eq = free_usd
        equity_series[warmup:] = np.maximum(100.0, equity_series[warmup:])

        # Calculate metrics
        cum_ret = ((final_eq - initial_capital) / initial_capital) * 100.0
        ret_series = pd.Series(equity_series[warmup:]).pct_change().dropna()

        ann_factor = np.sqrt(35040)
        mean_r = ret_series.mean()
        std_r = ret_series.std()
        sharpe = float((mean_r / std_r) * ann_factor) if std_r > 1e-8 else 0.0

        down_r = ret_series[ret_series < 0]
        down_std = down_r.std()
        sortino = float((mean_r / down_std) * ann_factor) if down_std > 1e-8 else 0.0

        roll_max = pd.Series(equity_series[warmup:]).cummax()
        dd_s = (roll_max - equity_series[warmup:]) / roll_max
        max_dd = float(dd_s.max() * 100.0)

        n_years = len(ret_series) / 35040
        ann_ret = float(((final_eq / initial_capital) ** (1.0 / max(0.01, n_years))) - 1.0)
        calmar = float(ann_ret / (max_dd / 100.0)) if max_dd > 1e-4 else 0.0

        composite = (0.4 * sortino) + (0.3 * sharpe) + (0.3 * calmar)

        n_trades = len(trade_pnls)
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        win_rate = (len(wins) / n_trades * 100.0) if n_trades > 0 else 0.0
        p_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (1.0 if wins else 0.0)

        return FastSimResult(
            cum_ret_pct=cum_ret,
            max_dd_pct=max_dd,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            composite_score=composite,
            total_trades=n_trades,
            win_rate=win_rate,
            profit_factor=p_factor,
        )


def run_calibration():
    print("=" * 80)
    print(" ALPHA CHAIN SYSTEMATIC PARAMETER CALIBRATION & RL META-CONTROLLER")
    print("=" * 80)

    # 1. Load Datasets
    cache_15d = Path("data/cache_15d_simulation.pkl")
    cache_jan = Path("data/cache_january_simulation.pkl")

    if not cache_15d.exists():
        print(" 15d cache missing. Run backtest first.")
        return

    with open(cache_15d, "rb") as f:
        d15_15m, d15_1h = pickle.load(f)

    trainer = FastVectorizedBacktester(d15_15m, d15_1h)

    # Load OOS dataset if available
    validator: Optional[FastVectorizedBacktester] = None
    if cache_jan.exists():
        with open(cache_jan, "rb") as f:
            jan_15m, jan_1h = pickle.load(f)
        validator = FastVectorizedBacktester(jan_15m, jan_1h)
        print(" Loaded January 2026 Out-Of-Sample validation dataset.")

    # 2. Objective Function for Global Optimization
    # Parameters to optimize:
    # 0: w_lag       [-0.8, 0.8]
    # 1: w_mom       [0.1, 1.2]
    # 2: w_rev       [-0.8, 0.8]
    # 3: w_flow      [-0.8, 0.8]
    # 4: adx_thresh  [17.0, 24.0]
    # 5: cost_hurdle [0.0010, 0.0022]
    # 6: min_conv    [0.38, 0.60]
    # 7: sl_mult     [1.1, 2.0]
    # 8: tp_mult     [1.4, 3.2]
    # 9: be_mult     [0.9, 1.6]
    # 10: max_hold   [16.0, 32.0]

    bounds = [
        (-0.8, 0.8),    # w_lag
        (0.1, 1.2),     # w_mom
        (-0.8, 0.8),    # w_rev
        (-0.8, 0.8),    # w_flow
        (17.0, 24.0),   # adx_thresh
        (0.0010, 0.0022),# cost_hurdle
        (0.38, 0.60),   # min_conviction
        (1.1, 2.0),     # sl_mult
        (1.4, 3.2),     # tp_mult
        (0.9, 1.6),     # be_mult
        (16.0, 32.0),   # max_holding_bars
    ]

    iteration_count = 0
    best_score = -9999.0

    def objective(params: np.ndarray) -> float:
        nonlocal iteration_count, best_score
        iteration_count += 1

        w = params[:4]
        adx_t = params[4]
        c_hurdle = params[5]
        min_c = params[6]
        sl_m = params[7]
        tp_m = params[8]
        be_m = params[9]
        max_h = int(params[10])

        res = trainer.simulate(
            weights=w,
            adx_trend_thresh=adx_t,
            cost_hurdle=c_hurdle,
            min_conviction=min_c,
            stop_loss_mult=sl_m,
            take_profit_mult=tp_m,
            breakeven_mult=be_m,
            max_holding_bars=max_h,
        )

        # Fitness Calculation
        score = res.composite_score

        # Penalties:
        # 1. Hard Drawdown Penalty (strict safety target at 2.90%)
        if res.max_dd_pct > 2.90:
            score -= 100.0 * (res.max_dd_pct - 2.90) ** 2

        # 2. Minimum Trade Count Penalty (Hackathon Q27: active bots favored)
        if res.total_trades < 30:
            score -= 1.5 * (30 - res.total_trades)

        # 3. Prefer positive cumulative return
        if res.cum_ret_pct < 0:
            score -= 3.0 * abs(res.cum_ret_pct)
        else:
            score += 2.0 * res.cum_ret_pct

        if score > best_score and res.max_dd_pct < 3.2 and res.total_trades >= 30:
            best_score = score
            print(f"  [Iter {iteration_count:3d}] New Best Score: {score:+.3f} | Ret: {res.cum_ret_pct:+.2f}% | MaxDD: {res.max_dd_pct:.2f}% | Trades: {res.total_trades} | Win: {res.win_rate:.1f}%")

        # SciPy minimizes, so return -score
        return -score

    print("\n Launching Differential Evolution Global Optimization...")
    t_start = time.time()
    opt_result = differential_evolution(
        objective,
        bounds=bounds,
        maxiter=12,
        popsize=8,
        mutation=(0.5, 1.0),
        recombination=0.7,
        seed=42,
        disp=False,
    )
    t_elapsed = time.time() - t_start
    print(f"\n Optimization complete in {t_elapsed:.1f}s ({iteration_count} evaluations)!")

    best_p = opt_result.x
    best_weights = best_p[:4]
    best_adx = float(best_p[4])
    best_cost_hurdle = float(best_p[5])
    best_min_conv = float(best_p[6])
    best_sl = float(best_p[7])
    best_tp = float(best_p[8])
    best_be = float(best_p[9])
    best_max_hold = int(best_p[10])

    # Run full in-sample benchmark with optimal parameters
    is_res = trainer.simulate(
        weights=best_weights,
        adx_trend_thresh=best_adx,
        cost_hurdle=best_cost_hurdle,
        min_conviction=best_min_conv,
        stop_loss_mult=best_sl,
        take_profit_mult=best_tp,
        breakeven_mult=best_be,
        max_holding_bars=best_max_hold,
    )

    print("\n" + "=" * 80)
    print(" IN-SAMPLE CALIBRATION RESULTS (15-Day Recent Binance Data)")
    print("=" * 80)
    print(f"  Cumulative Return:     {is_res.cum_ret_pct:+.2f}%")
    print(f"  Max Drawdown:          {is_res.max_dd_pct:.2f}% (Limit: < 4.00%)")
    print(f"  Sharpe Ratio:          {is_res.sharpe:.3f}")
    print(f"  Sortino Ratio:         {is_res.sortino:.3f}")
    print(f"  Calmar Ratio:          {is_res.calmar:.3f}")
    print(f"  Composite Score:       {is_res.composite_score:.3f}")
    print(f"  Total Trades:          {is_res.total_trades} ({is_res.win_rate:.1f}% Win Rate)")
    print(f"  Profit Factor:         {is_res.profit_factor:.2f}")

    # Out-Of-Sample Validation on January 2026 Shock
    if validator is not None:
        oos_res = validator.simulate(
            weights=best_weights,
            adx_trend_thresh=best_adx,
            cost_hurdle=best_cost_hurdle,
            min_conviction=best_min_conv,
            stop_loss_mult=best_sl,
            take_profit_mult=best_tp,
            breakeven_mult=best_be,
            max_holding_bars=best_max_hold,
        )
        print("\n" + "=" * 80)
        print(" OUT-OF-SAMPLE CROSS-VALIDATION RESULTS (January 2026 Severe Shock)")
        print("=" * 80)
        print(f"  OOS Cumulative Return: {oos_res.cum_ret_pct:+.2f}%")
        print(f"  OOS Max Drawdown:      {oos_res.max_dd_pct:.2f}% (Limit: < 4.00%)")
        print(f"  OOS Total Trades:      {oos_res.total_trades} ({oos_res.win_rate:.1f}% Win Rate)")
        print(f"  OOS Profit Factor:     {oos_res.profit_factor:.2f}")

    # Export Calibrated Configuration
    calibrated_config = {
        "profile_name": "calibrated_optimal",
        "factor_weights": {
            "lead_lag": round(float(best_weights[0]), 4),
            "idio_mom": round(float(best_weights[1]), 4),
            "reversal": round(float(best_weights[2]), 4),
            "vol_flow": round(float(best_weights[3]), 4),
        },
        "adx_trend_threshold": round(best_adx, 2),
        "cost_hurdle": round(best_cost_hurdle, 5),
        "min_conviction": round(best_min_conv, 3),
        "stop_loss_atr_mult": round(best_sl, 2),
        "take_profit_atr_mult": round(best_tp, 2),
        "breakeven_atr_mult": round(best_be, 2),
        "max_holding_bars": best_max_hold,
        "is_return_pct": round(is_res.cum_ret_pct, 2),
        "is_max_dd_pct": round(is_res.max_dd_pct, 2),
        "is_composite_score": round(is_res.composite_score, 3),
    }

    out_json = Path("config/calibrated_optimal_profile.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(calibrated_config, f, indent=2)
    print(f"\n Saved optimal calibrated profile to: {out_json}")
    return calibrated_config


if __name__ == "__main__":
    run_calibration()
