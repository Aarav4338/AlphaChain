"""
CarrotQuant Multi-Dataset 45-Day Out-of-Sample Backtesting & Validation Suite.
Partitions 45 days of continuous historical Binance crypto data
into 3 distinct non-overlapping 15-day tournament windows:
- Dataset 1 (Recent): Bars 2880 to 4320 (Most recent 15 days)
- Dataset 2 (Intermediate): Bars 1440 to 2880 (Prior 15 days)
- Dataset 3 (Early): Bars 0 to 1440 (Earliest 15 days)

Computes full tournament metrics for each dataset:
- Cumulative Return (%)
- Max Drawdown (%) [Must be < 4.0% on ALL datasets]
- Win Rate (%) & Profit Factor
- Sharpe, Sortino, Calmar, and Hackathon Composite Score
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe UTF-8 encoding for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config.trading_config import trading_config
from client.binance_fetcher import BinanceFetcher
from scripts.run_backtest import BacktestEngine, BacktestMetrics


class MultiDatasetBacktestHarness:
    """Evaluates strategy robustness across multiple non-overlapping time horizons."""

    def __init__(
        self,
        pairs: List[str] = None,
        initial_capital: float = 100000.0,
    ):
        self.pairs = pairs or trading_config.ASSET_UNIVERSE
        self.initial_capital = initial_capital
        self.fetcher = BinanceFetcher()
        self.engine = BacktestEngine(pairs=self.pairs, initial_capital=self.initial_capital)

    def fetch_full_history(self) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
        """Fetch ~4,360 15m candles (~45 days) and ~1,100 1h candles across universe."""
        print("=" * 75)
        print("📥 Fetching 45-Day Historical Data (~4,360 x 15m, 1,100 x 1h) from Binance...")
        print("=" * 75)
        data_15m = {}
        data_1h = {}
        for pair in self.pairs:
            print(f"  Fetching {pair}...")
            df_15m = self.fetcher.fetch_klines(pair, interval="15m", limit=4360)
            df_1h = self.fetcher.fetch_klines(pair, interval="1h", limit=1100)
            if df_15m is not None and len(df_15m) > 1000 and df_1h is not None and len(df_1h) > 200:
                data_15m[pair] = df_15m
                data_1h[pair] = df_1h
            else:
                print(f"  ⚠️ Warning: Insufficient data for {pair}")

        return data_15m, data_1h

    def partition_datasets(
        self, data_15m: Dict[str, pd.DataFrame], data_1h: Dict[str, pd.DataFrame]
    ) -> List[Tuple[str, Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], pd.Timestamp, pd.Timestamp]]:
        """
        Partition common timestamps into 3 non-overlapping 15-day windows (1440 bars each + 40 warmup).
        """
        common_ts = sorted(
            list(
                set.intersection(
                    *[set(df["timestamp"].dt.tz_convert("UTC")) for df in data_15m.values()]
                )
            )
        )
        total_bars = len(common_ts)
        print(f"\n📊 Total synchronized 15m bars available: {total_bars} (~{total_bars / 96:.1f} days)")

        if total_bars < 2880:
            raise ValueError(f"Need at least 2,880 bars for multi-dataset test (got {total_bars})")

        warmup = 40
        chunks = []

        # Dataset 3: Earliest
        ts_c3 = common_ts[: 1440 + warmup]
        # Dataset 2: Intermediate
        start_c2 = max(0, total_bars // 2 - 720 - warmup)
        ts_c2 = common_ts[start_c2 : start_c2 + 1440 + warmup]
        # Dataset 1: Most Recent
        ts_c1 = common_ts[-(1440 + warmup) :]

        dataset_defs = [
            ("Dataset 3 (Earliest 15 Days)", ts_c3),
            ("Dataset 2 (Intermediate 15 Days)", ts_c2),
            ("Dataset 1 (Most Recent 15 Days)", ts_c1),
        ]

        for name, ts_slice in dataset_defs:
            d15_slice = {}
            min_ts = ts_slice[0]
            max_ts = ts_slice[-1]
            for p, df in data_15m.items():
                d15_slice[p] = df[df["timestamp"].isin(ts_slice)].copy()

            d1h_slice = {}
            for p, df in data_1h.items():
                d1h_slice[p] = df[df["timestamp"] <= max_ts].copy()

            chunks.append((name, d15_slice, d1h_slice, min_ts, max_ts))

        return chunks

    def run_multi_dataset_eval(self):
        data_15m, data_1h = self.fetch_full_history()
        chunks = self.partition_datasets(data_15m, data_1h)

        results = []

        print("\n" + "=" * 80)
        print("🚀 EXECUTING MULTI-DATASET STRESS TEST (3 DISTINCT 15-DAY WINDOWS)")
        print("=" * 80)

        for name, d15, d1h, min_ts, max_ts in chunks:
            print(f"\n▶ Running {name}...")
            print(f"  Timeline: {str(min_ts)[:10]} to {str(max_ts)[:10]} ({len(next(iter(d15.values())))} bars)")

            engine = BacktestEngine(pairs=self.pairs, initial_capital=self.initial_capital)
            equity_df, metrics, trades = engine.run_simulation(d15, d1h)

            active_days = set()
            for t in trades:
                exit_t = t.get("exit_time", "")
                if len(exit_t) >= 10:
                    active_days.add(exit_t[:10])
                entry_t = t.get("entry_time", "")
                if len(entry_t) >= 10 and not entry_t.startswith("Bar") and not entry_t.startswith("Early"):
                    active_days.add(entry_t[:10])

            n_days = len(active_days)
            days_pass = n_days >= 10

            results.append({
                "Dataset": name,
                "Period": f"{str(min_ts)[:10]} ~ {str(max_ts)[:10]}",
                "Final Equity": metrics.final_equity,
                "Return %": metrics.cumulative_return_pct,
                "Max DD %": metrics.max_drawdown_pct,
                "Trades": metrics.total_trades,
                "Active Days": f"{n_days}/15",
                "Days Status": "PASS" if days_pass else "FAIL (<10d)",
                "Win Rate %": metrics.win_rate_pct,
                "Profit Factor": metrics.profit_factor,
                "Sharpe": metrics.sharpe_ratio,
                "Sortino": metrics.sortino_ratio,
                "Calmar": metrics.calmar_ratio,
                "Composite": metrics.hackathon_composite_score,
                "DD Status": "PASS" if metrics.max_drawdown_pct < 4.0 else "FAIL (>4%)",
            })

        print("\n" + "=" * 105)
        print("🏆 MULTI-DATASET PERFORMANCE COMPARISON TABLE")
        print("=" * 105)
        hdr = f"{'DATASET':<32} {'RETURN%':<10} {'MAX DD%':<10} {'TRADES':<8} {'DAYS':<9} {'WIN%':<8} {'PF':<7} {'SHARPE':<8} {'COMPOSITE':<11} {'STATUS'}"
        print(hdr)
        print("-" * 105)
        for r in results:
            overall_pass = r["DD Status"] == "PASS" and r["Days Status"] == "PASS"
            status_str = "100% PASS" if overall_pass else "FAIL"
            row = (
                f"{r['Dataset']:<32} "
                f"{r['Return %']:>+7.2f}%   "
                f"{r['Max DD %']:>6.2f}%   "
                f"{r['Trades']:>6}   "
                f"{r['Active Days']:<9} "
                f"{r['Win Rate %']:>5.1f}%  "
                f"{r['Profit Factor']:>5.2f} "
                f"{r['Sharpe']:>7.2f} "
                f"{r['Composite']:>9.2f}   "
                f"{status_str}"
            )
            print(row)
        print("=" * 105)

        import json
        out_file = Path("docs/multi_dataset_results.json")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"💾 Multi-dataset results exported to: {out_file}")


def main():
    harness = MultiDatasetBacktestHarness()
    harness.run_multi_dataset_eval()


if __name__ == "__main__":
    main()

