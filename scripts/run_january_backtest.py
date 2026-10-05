"""
CarrotQuant January 15 – January 30 Out-of-Sample Historical Backtest.
Evaluates strategy performance, active trading days (>= 10 requirement),
and drawdown constraint (< 4.0%) on the January 15th to January 30th window.
"""
import sys
import time
from pathlib import Path
import datetime
import requests
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
from data.candle_manager import CandleManager
from strategy.regime_detector import RegimeDetector
from strategy.multi_regime_alpha import MultiRegimeAlphaStrategy
from strategy.signals import SignalAction
from risk.position_sizer import PositionSizer
from risk.stop_loss_manager import StopLossManager
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus


def fetch_binance_range(
    symbol: str,
    interval: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
) -> pd.DataFrame:
    """Fetch complete klines between start_dt and end_dt with forward pagination."""
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    url = "https://api.binance.com/api/v3/klines"

    all_rows = []
    curr_start = start_ms

    while curr_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": curr_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data or not isinstance(data, list):
            break

        all_rows.extend(data)
        last_open_time = data[-1][0]
        if last_open_time <= curr_start:
            break
        curr_start = last_open_time + 1

        if len(data) < 1000:
            break
        time.sleep(0.05)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_rows,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
        ],
    )
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df.rename(columns={"open_time": "timestamp"}, inplace=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_values("timestamp", inplace=True)
    df.drop_duplicates(subset=["timestamp"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def run_january_validation():
    print("=" * 85)
    print("📅 CARROTQUANT OUT-OF-SAMPLE VALIDATION: JANUARY 15th TO JANUARY 30th")
    print("=" * 85)

    fetcher = BinanceFetcher()
    pairs = trading_config.ASSET_UNIVERSE

    # Define time windows:
    # 15m active window: 2026-01-15 00:00 to 2026-01-30 23:59 UTC
    # 15m warmup: 40 bars (~10 hours) before Jan 15 00:00 -> 2026-01-14 14:00 UTC
    # 1h warmup: 120 hours before Jan 15 00:00 -> 2026-01-10 00:00 UTC
    warmup_start_15m = datetime.datetime(2026, 1, 14, 14, 0, tzinfo=datetime.timezone.utc)
    warmup_start_1h = datetime.datetime(2026, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    sim_end = datetime.datetime(2026, 1, 30, 23, 59, 59, tzinfo=datetime.timezone.utc)

    print(f"📥 Fetching 15m & 1h data across {len(pairs)} pairs (Jan 10 – Jan 30)...")
    data_15m = {}
    data_1h = {}

    for pair in pairs:
        sym = fetcher.to_binance_symbol(pair)
        print(f"  Fetching {pair} ({sym})...")
        df_15 = fetch_binance_range(sym, "15m", warmup_start_15m, sim_end)
        df_1h = fetch_binance_range(sym, "1h", warmup_start_1h, sim_end)

        if len(df_15) >= 1400 and len(df_1h) >= 300:
            data_15m[pair] = df_15
            data_1h[pair] = df_1h
        else:
            print(f"  ⚠️ Warning: Insufficient data for {pair} (15m: {len(df_15)}, 1h: {len(df_1h)})")

    common_ts = sorted(
        list(
            set.intersection(
                *[set(df["timestamp"].dt.tz_convert("UTC")) for df in data_15m.values()]
            )
        )
    )
    print(f"\n📊 Total synchronized 15m bars: {len(common_ts)}")
    print(f"  Start: {common_ts[0]} | End: {common_ts[-1]}")

    # Identify index where active simulation begins (Jan 15 00:00 UTC)
    active_start_dt = datetime.datetime(2026, 1, 15, 0, 0, tzinfo=datetime.timezone.utc)
    warmup_bars = 40
    for idx, t in enumerate(common_ts):
        if t >= active_start_dt:
            warmup_bars = idx
            break

    print(f"  Warmup bars: {warmup_bars} (Warmup ends at {common_ts[warmup_bars]})")
    print(f"  Active simulation bars: {len(common_ts) - warmup_bars} bars (~{(len(common_ts) - warmup_bars)/96:.1f} days)")

    # Execute simulation
    initial_capital = 100000.0
    fee_rate = trading_config.MAKER_FEE_RATE  # 0.05%
    cm = CandleManager(max_buffer_size=400)
    regime_det = RegimeDetector()
    strat = MultiRegimeAlphaStrategy(regime_det)
    sizer = PositionSizer()
    slm = StopLossManager()
    cb = CircuitBreaker(initial_equity=initial_capital)

    d1h_ts_map = {p: df.sort_values("timestamp").reset_index(drop=True) for p, df in data_1h.items()}

    equity = initial_capital
    free_usd = initial_capital
    trade_pnls = []
    executed_trades = []
    equity_records = []
    trading_days = set()

    print("\n🚀 Executing simulation with calibrated risk limits (0.35% risk, max 2 concurrent positions)...")

    for t_idx in range(warmup_bars, len(common_ts)):
        ts = common_ts[t_idx]

        # 1. Provide exact point-in-time candles up to ts
        for p, df in data_15m.items():
            sub_15 = df[df["timestamp"] <= ts].tail(100)
            cm.initialize_pair_history(p, "15m", sub_15)

        for p, df_1h_full in d1h_ts_map.items():
            sub_1h = df_1h_full[df_1h_full["timestamp"] <= ts].tail(100)
            cm.initialize_pair_history(p, "1h", sub_1h)

        # 2. Check exits
        for p in list(slm.positions.keys()):
            pos = slm.positions.get(p)
            if not pos: continue
            curr_p = cm.get_latest_price(p) or pos.entry_price
            exit_sig = slm.update_price(p, curr_p, current_bar=t_idx)
            if exit_sig:
                if "Stop" in exit_sig.reason:
                    fill_p = pos.stop_loss * (1.0 - 0.001 if pos.side == SignalAction.BUY else 1.0 + 0.001)
                elif "Take Profit" in exit_sig.reason:
                    fill_p = pos.take_profit
                else:
                    fill_p = curr_p

                pnl = (fill_p - pos.entry_price) * pos.quantity if pos.side == SignalAction.BUY else (pos.entry_price - fill_p) * pos.quantity
                fee = (fill_p * pos.quantity) * fee_rate
                net_pnl = pnl - fee
                free_usd += (pos.quantity * fill_p if pos.side == SignalAction.BUY else pos.collateral + net_pnl)

                trade_pnls.append(net_pnl)
                executed_trades.append({
                    "trade_id": len(executed_trades) + 1,
                    "pair": p,
                    "side": pos.side.name,
                    "entry_time": getattr(pos, "entry_time_str", f"Bar {pos.entry_bar}"),
                    "exit_time": str(ts)[:19],
                    "entry_price": float(pos.entry_price),
                    "exit_price": float(fill_p),
                    "pct_move": float(((fill_p - pos.entry_price) / pos.entry_price) * 100 if pos.side == SignalAction.BUY else ((pos.entry_price - fill_p) / pos.entry_price) * 100),
                    "net_pnl": float(net_pnl),
                    "exit_reason": exit_sig.reason,
                })
                trading_days.add(str(ts)[:10])
                slm.remove_position(p)
                strat.record_exit(p, t_idx, net_pnl=net_pnl)

        # 3. Live equity
        pos_val = 0.0
        for p, pos in slm.positions.items():
            curr_p = cm.get_latest_price(p) or pos.entry_price
            if pos.side == SignalAction.BUY:
                pos_val += pos.quantity * curr_p
            else:
                unrealized = (pos.entry_price - curr_p) * pos.quantity
                pos_val += max(0.0, pos.collateral + unrealized)
        equity = free_usd + pos_val
        equity_records.append({"timestamp": ts, "equity": equity})

        # 4. Circuit Breaker
        cb_status = cb.update_equity(equity, current_time=ts.timestamp())
        if cb_status == CircuitBreakerStatus.TRIPPED:
            for p, pos in list(slm.positions.items()):
                curr_p = cm.get_latest_price(p) or pos.entry_price
                fee = (curr_p * pos.quantity) * fee_rate
                pnl = (curr_p - pos.entry_price) * pos.quantity - fee
                free_usd += (pos.quantity * curr_p) - fee
                trade_pnls.append(pnl)
                trading_days.add(str(ts)[:10])
                slm.remove_position(p)
                strat.record_exit(p, t_idx, net_pnl=pnl)
            cb.acknowledge_liquidation()

        # 5. Entries
        if cb.is_trading_allowed():
            sigs = strat.generate_signals(cm, active_pairs=list(slm.positions.keys()), current_bar_index=t_idx)
            if sigs:
                sigs.sort(key=lambda s: s.conviction, reverse=True)
                if getattr(trading_config, "DYNAMIC_CONCURRENCY_ENABLED", True):
                    reg_res = reg.detect_regime(cm)
                    current_heat = slm.get_portfolio_risk_heat(equity)
                    sized_orders = sizer.calculate_dynamic_portfolio_sizes(
                        signals=sigs,
                        portfolio_equity=equity,
                        free_usd=free_usd,
                        current_open_risk_heat=current_heat,
                        regime=reg_res.regime,
                    )
                    for order in sized_orders:
                        if not order.allowed:
                            continue
                        sig = next((s for s in sigs if s.pair == order.pair), None)
                        if sig is None:
                            continue
                        cost = order.estimated_capital
                        fee = cost * fee_rate
                        if free_usd >= (cost + fee):
                            free_usd -= (cost + fee)
                            slm.register_position(
                                pair=sig.pair,
                                side=sig.action,
                                entry_price=sig.entry_price,
                                quantity=order.quantity,
                                collateral=order.collateral if sig.action == SignalAction.SHORT_OPEN else 0.0,
                                stop_loss=sig.stop_loss_price,
                                take_profit=sig.take_profit_price,
                                atr=sig.atr,
                                entry_bar=t_idx,
                            )
                            pos = slm.positions.get(sig.pair)
                            if pos:
                                pos.entry_time_str = str(ts)[:19]
                            trading_days.add(str(ts)[:10])
                else:
                    slots = trading_config.MAX_CONCURRENT_POSITIONS - slm.get_open_positions_count()
                    if slots > 0:
                        for sig in sigs[:slots]:
                            s_res = sizer.calculate_size(sig, equity, free_usd, slm.get_open_positions_count())
                            cost = s_res.estimated_capital
                            fee = cost * fee_rate
                            if s_res.allowed and free_usd >= (cost + fee):
                                free_usd -= (cost + fee)
                                slm.register_position(
                                    pair=sig.pair,
                                    side=sig.action,
                                    entry_price=sig.entry_price,
                                    quantity=s_res.quantity,
                                    collateral=0.0,
                                    stop_loss=sig.stop_loss_price,
                                    take_profit=sig.take_profit_price,
                                    atr=sig.atr,
                                    entry_bar=t_idx,
                                )
                                pos = slm.positions.get(sig.pair)
                                if pos:
                                    pos.entry_time_str = str(ts)[:19]
                                trading_days.add(str(ts)[:10])

    # End close
    final_ts = common_ts[-1]
    for p, pos in list(slm.positions.items()):
        curr_p = cm.get_latest_price(p) or pos.entry_price
        fee = (curr_p * pos.quantity) * fee_rate
        pnl = (curr_p - pos.entry_price) * pos.quantity - fee
        free_usd += (pos.quantity * curr_p) - fee
        trade_pnls.append(pnl)
        executed_trades.append({
            "trade_id": len(executed_trades) + 1,
            "pair": p,
            "side": pos.side.name,
            "entry_time": getattr(pos, "entry_time_str", f"Bar {pos.entry_bar}"),
            "exit_time": str(final_ts)[:19],
            "entry_price": float(pos.entry_price),
            "exit_price": float(curr_p),
            "pct_move": float(((curr_p - pos.entry_price) / pos.entry_price) * 100),
            "net_pnl": float(pnl),
            "exit_reason": "End of Simulation Close",
        })
        trading_days.add(str(final_ts)[:10])
        slm.remove_position(p)

    final_equity = free_usd
    eq_df = pd.DataFrame(equity_records)
    cum_ret = ((final_equity - initial_capital) / initial_capital) * 100.0
    rolling_max = eq_df["equity"].cummax()
    max_dd = float(((rolling_max - eq_df["equity"]) / rolling_max).max() * 100.0)

    rets = eq_df["equity"].pct_change().dropna()
    ann_factor = np.sqrt(35040)
    sharpe = float((rets.mean() / (rets.std() + 1e-8)) * ann_factor)
    downside = rets[rets < 0.0]
    sortino = float((rets.mean() / (downside.std() + 1e-8)) * ann_factor) if len(downside) > 0 else 0.0
    n_years = len(rets) / 35040
    ann_ret = float(((final_equity / initial_capital) ** (1.0 / max(0.01, n_years))) - 1.0)
    calmar = float(ann_ret / (max_dd / 100.0)) if max_dd > 0.01 else 0.0
    comp = 0.4 * sortino + 0.3 * sharpe + 0.3 * calmar

    win_trades = [p for p in trade_pnls if p > 0]
    win_rate = len(win_trades) / len(trade_pnls) * 100.0 if trade_pnls else 0.0
    gross_win = sum(win_trades) if win_trades else 0.0
    gross_loss = abs(sum([p for p in trade_pnls if p < 0])) if [p for p in trade_pnls if p < 0] else 1e-8
    pf = gross_win / gross_loss

    print("\n" + "=" * 85)
    print("🏆 JANUARY 15 – JANUARY 30 BACKTEST SCORECARD")
    print("=" * 85)
    print(f"  Calendar Window:          January 15, 2026 to January 30, 2026 (16 days)")
    print(f"  Starting Portfolio:       ${initial_capital:,.2f} USD")
    print(f"  Ending Portfolio Equity:  ${final_equity:,.2f} USD")
    print(f"  Net Cumulative Return:    {cum_ret:+.2f}%")
    print(f"  Maximum Drawdown:         {max_dd:.2f}% (Limit: < 4.0% -> {'PASS' if max_dd < 4.0 else 'FAIL'})")
    print(f"  Total Trades Executed:    {len(trade_pnls)} trades")
    print(f"  Active Trading Days:      {len(trading_days)} days (Rule: >= 10 days -> {'PASS' if len(trading_days) >= 10 else 'FAIL'})")
    print(f"  Active Days List:         {sorted(list(trading_days))}")
    print(f"  Win Rate:                 {win_rate:.1f}% ({len(win_trades)} wins / {len(trade_pnls)} trades)")
    print(f"  Profit Factor:            {pf:.2f}")
    print(f"  Annualized Sharpe:        {sharpe:.2f}")
    print(f"  Downside Sortino:         {sortino:.2f}")
    print(f"  Calmar Ratio:             {calmar:.2f}")
    print(f"  Hackathon Composite Score:{comp:.2f}")
    print("=" * 85)

    print("\n📜 TRADE-BY-TRADE LEDGER:")
    print(f"{'#':<3} {'PAIR':<10} {'SIDE':<6} {'ENTRY TIME':<19} {'EXIT TIME':<19} {'MOVE%':<8} {'NET PNL':<10} {'EXIT REASON'}")
    print("-" * 105)
    for t in executed_trades:
        print(
            f"{t['trade_id']:<3} {t['pair']:<10} {t['side']:<6} {t['entry_time']:<19} {t['exit_time']:<19} "
            f"{t['pct_move']:>+6.2f}%   ${t['net_pnl']:>+8.2f}  {t['exit_reason']}"
        )
    print("=" * 105)

    import json
    out_data = {
        "period": "2026-01-15 ~ 2026-01-30",
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "cumulative_return_pct": cum_ret,
        "max_drawdown_pct": max_dd,
        "total_trades": len(trade_pnls),
        "active_trading_days": len(trading_days),
        "days_list": sorted(list(trading_days)),
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "composite_score": comp,
        "trades": executed_trades,
    }
    out_file = Path("docs/january_backtest_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    print(f"\n💾 Results exported to: {out_file}")


if __name__ == "__main__":
    run_january_validation()

