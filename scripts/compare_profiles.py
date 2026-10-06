"""
Comparative Profile Backtest Engine: Baseline vs. Calibrated Optimal vs. Pyramiding Optimal.
Fetches historical market data once, then executes identical historical
replay through all 3 risk & allocation profiles to produce empirical metrics.
"""
import sys
import os
import json
from pathlib import Path
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config.trading_config import trading_config
from client.binance_fetcher import BinanceFetcher
from scripts.run_backtest import BacktestEngine, BacktestMetrics


def run_profile_test(limit_15m: int = 1480, limit_1h: int = 400):
    print("=" * 105)
    print("🔬 EMPIRICAL BACKTEST: BASELINE vs. CALIBRATED OPTIMAL vs. PYRAMIDING OPTIMAL")
    print("=" * 105)

    engine = BacktestEngine()
    print(f"📥 Fetching synchronized candles ({limit_15m} x 15m, {limit_1h} x 1h) across universe...")
    data_15m, data_1h = engine.fetch_historical_data(limit_15m=limit_15m, limit_1h=limit_1h)

    if not data_15m or len(data_15m) < 2:
        print("❌ Insufficient data fetched.")
        return

    # Profile 1: Baseline Sniper (Ultra-Defensive)
    print("\n[RUN 1/3] Simulating BASELINE SNIPER Profile (Ultra-Defensive)...")
    trading_config.apply_profile("baseline_sniper")
    eq1_df, m1, trades1 = engine.run_simulation(data_15m, data_1h)

    # Profile 2: Calibrated Optimal (Live Bot Profile)
    print("\n[RUN 2/3] Simulating CALIBRATED OPTIMAL Profile (Current Live AWS Daemon)...")
    trading_config.apply_profile("calibrated_optimal")
    eq2_df, m2, trades2 = engine.run_simulation(data_15m, data_1h)

    # Profile 3: Pyramiding Optimal (Trend Scale-In on Winners)
    print("\n[RUN 3/3] Simulating PYRAMIDING OPTIMAL Profile (Trend Scale-In On Winners)...")
    trading_config.apply_profile("pyramiding_optimal")
    eq3_df, m3, trades3 = engine.run_simulation(data_15m, data_1h)

    # Reset trading_config back to calibrated_optimal for safety
    trading_config.apply_profile("calibrated_optimal")

    print("\n" + "=" * 105)
    print("📊 COMPARATIVE PROFILE RESULTS SCORECARD")
    print("=" * 105)
    print(f"{'Metric':<25} | {'Baseline Sniper':<22} | {'Calibrated Optimal':<22} | {'Pyramiding Optimal':<22}")
    print("-" * 105)
    print(f"{'Initial Capital':<25} | ${100000.0:,.2f}               | ${100000.0:,.2f}               | ${100000.0:,.2f}")
    print(f"{'Final Equity':<25} | ${m1.final_equity:,.2f}               | ${m2.final_equity:,.2f}               | ${m3.final_equity:,.2f}")
    print(f"{'Cumulative Return':<25} | {m1.cumulative_return_pct:>+8.2f}%                 | {m2.cumulative_return_pct:>+8.2f}%                 | {m3.cumulative_return_pct:>+8.2f}%")
    print(f"{'Max Drawdown':<25} | {m1.max_drawdown_pct:>8.2f}%                 | {m2.max_drawdown_pct:>8.2f}%                 | {m3.max_drawdown_pct:>8.2f}%")
    print(f"{'Total Trades':<25} | {m1.total_trades:>8d}                  | {m2.total_trades:>8d}                  | {m3.total_trades:>8d}")
    print(f"{'Win Rate':<25} | {m1.win_rate_pct:>8.1f}%                 | {m2.win_rate_pct:>8.1f}%                 | {m3.win_rate_pct:>8.1f}%")
    print(f"{'Profit Factor':<25} | {m1.profit_factor:>8.2f}                  | {m2.profit_factor:>8.2f}                  | {m3.profit_factor:>8.2f}")
    print(f"{'Sharpe Ratio':<25} | {m1.sharpe_ratio:>8.2f}                  | {m2.sharpe_ratio:>8.2f}                  | {m3.sharpe_ratio:>8.2f}")
    print(f"{'Sortino Ratio':<25} | {m1.sortino_ratio:>8.2f}                  | {m2.sortino_ratio:>8.2f}                  | {m3.sortino_ratio:>8.2f}")
    print(f"{'Calmar Ratio':<25} | {m1.calmar_ratio:>8.2f}                  | {m2.calmar_ratio:>8.2f}                  | {m3.calmar_ratio:>8.2f}")
    print(f"{'Composite Score':<25} | {m1.hackathon_composite_score:>8.2f}                  | {m2.hackathon_composite_score:>8.2f}                  | {m3.hackathon_composite_score:>8.2f}")
    print("=" * 105)

    # Save to disk for inspection
    results = {
        "baseline_sniper": {
            "final_equity": m1.final_equity,
            "cumulative_return_pct": m1.cumulative_return_pct,
            "max_drawdown_pct": m1.max_drawdown_pct,
            "total_trades": m1.total_trades,
            "win_rate_pct": m1.win_rate_pct,
            "profit_factor": m1.profit_factor,
            "sharpe_ratio": m1.sharpe_ratio,
            "sortino_ratio": m1.sortino_ratio,
            "calmar_ratio": m1.calmar_ratio,
            "composite_score": m1.hackathon_composite_score,
            "trades_count": len(trades1),
        },
        "calibrated_optimal": {
            "final_equity": m2.final_equity,
            "cumulative_return_pct": m2.cumulative_return_pct,
            "max_drawdown_pct": m2.max_drawdown_pct,
            "total_trades": m2.total_trades,
            "win_rate_pct": m2.win_rate_pct,
            "profit_factor": m2.profit_factor,
            "sharpe_ratio": m2.sharpe_ratio,
            "sortino_ratio": m2.sortino_ratio,
            "calmar_ratio": m2.calmar_ratio,
            "composite_score": m2.hackathon_composite_score,
            "trades_count": len(trades2),
        },
        "pyramiding_optimal": {
            "final_equity": m3.final_equity,
            "cumulative_return_pct": m3.cumulative_return_pct,
            "max_drawdown_pct": m3.max_drawdown_pct,
            "total_trades": m3.total_trades,
            "win_rate_pct": m3.win_rate_pct,
            "profit_factor": m3.profit_factor,
            "sharpe_ratio": m3.sharpe_ratio,
            "sortino_ratio": m3.sortino_ratio,
            "calmar_ratio": m3.calmar_ratio,
            "composite_score": m3.hackathon_composite_score,
            "trades_count": len(trades3),
        },
    }
    out_file = Path("docs/profile_comparison_results.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Full results saved to: {out_file}")


if __name__ == "__main__":
    run_profile_test()

