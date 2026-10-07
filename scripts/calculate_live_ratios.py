"""
Calculate current live performance ratios (Sharpe, Sortino, Calmar)
from live Roostoo account orders, balance snapshots, and position state.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.roostoo_client import RoostooClient

def main():
    client = RoostooClient()

    # 1. Fetch current balance
    bal_res = client.get_balance() or {}
    wallet = bal_res.get("SpotWallet") or bal_res.get("Wallet", {})
    usd_info = wallet.get("USD", {})
    free_usd = float(usd_info.get("Free", 0.0))
    locked_usd = float(usd_info.get("Lock", 0.0))

    # Fetch ticker prices
    ticker_res = client.get_ticker() or {}
    tickers = ticker_res.get("Data", {})
    prices = {}
    for p, data in tickers.items():
        prices[p] = float(data.get("LastPrice", 0.0))

    # Spot positions
    spot_val = 0.0
    for coin, info in wallet.items():
        if coin == "USD":
            continue
        qty = float(info.get("Free", 0.0)) + float(info.get("Lock", 0.0))
        pair = f"{coin}/USD"
        p = prices.get(pair, 0.0)
        val = qty * p
        if val >= 10.0:
            spot_val += val

    # Short positions
    short_res = client.get_short_positions()
    raw_shorts = []
    if short_res:
        if isinstance(short_res, list):
            raw_shorts = short_res
        elif isinstance(short_res, dict):
            raw_shorts = short_res.get("Positions") or short_res.get("positions") or []
    if not isinstance(raw_shorts, list):
        raw_shorts = []

    total_short_collateral = 0.0
    total_short_pnl = 0.0
    for s in raw_shorts:
        pair = s.get("Pair", "N/A")
        collateral = float(s.get("Collateral", 0.0))
        entry_p = float(s.get("EntryPrice", 0.0))
        curr_p = float(s.get("CurrentPrice") or prices.get(pair, entry_p))
        qty = float(s.get("ShortQty", 0.0))
        pnl = float(s.get("UnrealizedPNL", 0.0))
        if pnl == 0.0 and entry_p > 0:
            pnl = (entry_p - curr_p) * qty
        total_short_collateral += collateral
        total_short_pnl += pnl

    unallocated_lock = max(0.0, locked_usd - total_short_collateral)
    shorts_active_equity = max(0.0, total_short_collateral + total_short_pnl)
    total_equity = free_usd + unallocated_lock + spot_val + shorts_active_equity
    initial_capital = 100000.0
    drawdown_pct = max(0.0, (initial_capital - total_equity) / initial_capital * 100.0)

    print("=====================================================================")
    print("           LIVE ACCOUNT REAL-TIME VALUATION & POSITION AUDIT        ")
    print("=====================================================================")
    print(f"Current Total Account Equity:  ${total_equity:,.2f} USD")
    print(f"Initial Starting Capital:      ${initial_capital:,.2f} USD")
    print(f"Total Cumulative Return:       {((total_equity - initial_capital)/initial_capital)*100:+.2f}%")
    print(f"Current Maximum Drawdown:      {drawdown_pct:.2f}% (Limit: < 4.00%)")
    print(f"Free Cash Buffer:              ${free_usd:,.2f} USD ({free_usd/total_equity*100:.1f}%)")
    print(f"Spot Holdings Valuation:       ${spot_val:,.2f} USD")
    print(f"Short Margin Collateral:       ${total_short_collateral:,.2f} USD")
    print(f"Active Short Unrealized PnL:   {total_short_pnl:+.2f} USD")

    # 2. Query all historical orders
    order_res = client.query_order(pending_only=False) or {}
    orders = order_res.get("OrderMatched", []) or order_res.get("OrderList", [])
    print(f"\nTotal Order History Records on Roostoo: {len(orders)}")

    # Parse filled trades and reconstruct trade PnLs
    filled_orders = [o for o in orders if o.get("Status") == "FILLED"]
    print(f"Filled Orders: {len(filled_orders)}")

    # Sort filled orders chronologically
    filled_orders.sort(key=lambda x: x.get("FinishTimestamp") or x.get("CreateTimestamp") or 0)

    # Let's inspect the trades
    trade_pnls = []
    # Match buys and sells per pair to compute realized trade returns
    # Also get fee deductions
    positions_tracker = {}
    total_fees = 0.0

    print("\n--- RECONSTRUCTED REALIZED TRADES ---")
    for o in filled_orders:
        pair = o.get("Pair")
        side = o.get("Side")
        price = float(o.get("FilledAverPrice", 0.0))
        qty = float(o.get("FilledQuantity", 0.0))
        fee = float(o.get("CommissionChargeValue", 0.0))
        total_fees += fee
        ts = o.get("FinishTimestamp") or o.get("CreateTimestamp")
        dt_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if side == "BUY":
            # Opening long
            if pair not in positions_tracker:
                positions_tracker[pair] = {"side": "LONG", "qty": qty, "price": price}
            else:
                curr = positions_tracker[pair]
                if curr["side"] == "LONG":
                    # add to long
                    tot_qty = curr["qty"] + qty
                    avg_p = (curr["qty"] * curr["price"] + qty * price) / tot_qty
                    curr["qty"] = tot_qty
                    curr["price"] = avg_p
        elif side == "SELL":
            # Closing long
            if pair in positions_tracker and positions_tracker[pair]["side"] == "LONG":
                entry_p = positions_tracker[pair]["price"]
                pnl = (price - entry_p) * qty
                trade_pnls.append(pnl)
                print(f"[{dt_str}] Closed LONG {pair}: Entry=${entry_p:.4f}, Exit=${price:.4f}, Qty={qty}, PnL=${pnl:+.2f}")
                positions_tracker[pair]["qty"] -= qty
                if positions_tracker[pair]["qty"] <= 1e-6:
                    del positions_tracker[pair]
        elif side == "SHORT_OPEN":
            positions_tracker[pair] = {"side": "SHORT", "qty": qty, "price": price}
        elif side == "SHORT_CLOSE":
            if pair in positions_tracker and positions_tracker[pair]["side"] == "SHORT":
                entry_p = positions_tracker[pair]["price"]
                pnl = (entry_p - price) * qty
                trade_pnls.append(pnl)
                print(f"[{dt_str}] Closed SHORT {pair}: Entry=${entry_p:.4f}, Exit=${price:.4f}, Qty={qty}, PnL=${pnl:+.2f}")
                positions_tracker[pair]["qty"] -= qty
                if positions_tracker[pair]["qty"] <= 1e-6:
                    del positions_tracker[pair]

    print(f"\nTotal Realized Closed Trades: {len(trade_pnls)}")
    print(f"Total Commissions / Fees Paid: ${total_fees:.2f} USD")
    if trade_pnls:
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        print(f"Winning Trades: {len(wins)}, Losing Trades: {len(losses)}, Win Rate: {len(wins)/len(trade_pnls)*100:.1f}%")
        print(f"Sum Closed Trade PnL: ${sum(trade_pnls):+.2f} USD")

    # Let's inspect local database snapshots to compute time-series returns & ratios
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "trading.db"
    snapshots_df = None
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        snapshots_df = pd.read_sql_query("SELECT * FROM portfolio_snapshots ORDER BY timestamp ASC", conn)
        conn.close()

    print("\n=====================================================================")
    print("         CALCULATION OF CURRENT SHARPE, SORTINO, AND CALMAR          ")
    print("=====================================================================")

    # We evaluate both:
    # 1. Closed Trade PnL sequence metrics (Trade-level returns)
    # 2. Account Equity Curve snapshot returns

    # Method A: Trade-Level Return Series
    if len(trade_pnls) >= 2:
        # returns as fraction of initial capital per trade
        trade_returns = np.array(trade_pnls) / initial_capital
        mean_tr = np.mean(trade_returns)
        std_tr = np.std(trade_returns, ddof=1)
        downside_std_tr = np.std([r for r in trade_returns if r < 0], ddof=1) if any(r < 0 for r in trade_returns) else 1e-6

        # Standard annualization factor for trades per year (assuming ~15 trades per day -> ~5475 trades/yr)
        annual_factor_trades = np.sqrt(5475)
        sharpe_trades = (mean_tr / std_tr) * annual_factor_trades if std_tr > 1e-8 else 0.0
        sortino_trades = (mean_tr / downside_std_tr) * annual_factor_trades if downside_std_tr > 1e-8 else 0.0

        # Calmar on trades
        # Max drawdown in equity terms
        eq_curve = [initial_capital]
        for pnl in trade_pnls:
            eq_curve.append(eq_curve[-1] + pnl)
        eq_arr = np.array(eq_curve)
        peak_arr = np.maximum.accumulate(eq_arr)
        dds = (peak_arr - eq_arr) / peak_arr
        max_dd_trade = float(np.max(dds))
        net_ret_trade = (eq_arr[-1] - initial_capital) / initial_capital
        # Annualized return over ~2 days
        ann_ret_trade = ((eq_arr[-1] / initial_capital) ** (365.0 / 2.0)) - 1.0 if eq_arr[-1] > 0 else -1.0
        calmar_trades = ann_ret_trade / max_dd_trade if max_dd_trade > 1e-6 else 0.0

        print("\n[Method A: Trade-Level Realized Returns (Closed Round-Trips)]")
        print(f"  Sample Size:          {len(trade_pnls)} closed round-trip trades")
        print(f"  Sharpe Ratio:         {sharpe_trades:+.2f}")
        print(f"  Sortino Ratio:        {sortino_trades:+.2f}")
        print(f"  Calmar Ratio:         {calmar_trades:+.2f}")
        print(f"  Max Drawdown:         {max_dd_trade * 100:.2f}%")

    # Method B: Hourly / Periodic Snapshot Returns (if snapshots available)
    if snapshots_df is not None and len(snapshots_df) >= 3:
        # Exclude initial warmup anomalies if any
        valid_snaps = snapshots_df[snapshots_df["total_equity"] > 50000.0].copy()
        if len(valid_snaps) >= 2:
            # append current real-time equity
            now_iso = datetime.now(timezone.utc).isoformat()
            new_row = pd.DataFrame([{
                "timestamp": now_iso,
                "total_equity": total_equity,
                "drawdown_pct": drawdown_pct
            }])
            combined = pd.concat([valid_snaps, new_row], ignore_index=True)
            combined["ret"] = combined["total_equity"].pct_change()
            returns = combined["ret"].dropna()

            mean_ret = returns.mean()
            std_ret = returns.std(ddof=1)
            downside = returns[returns < 0]
            downside_std = downside.std(ddof=1) if len(downside) > 1 else std_ret

            # Annualized factor for hourly snapshots: sqrt(24 * 365) = sqrt(8760)
            ann_factor_hourly = np.sqrt(8760)
            sharpe_hourly = (mean_ret / std_ret) * ann_factor_hourly if std_ret > 1e-8 else 0.0
            sortino_hourly = (mean_ret / downside_std) * ann_factor_hourly if downside_std > 1e-8 else 0.0

            # Calmar
            max_dd = combined["drawdown_pct"].max() / 100.0
            total_ret = (total_equity - initial_capital) / initial_capital
            # Elapsed days: from first timestamp to now
            t0 = pd.to_datetime(combined["timestamp"].iloc[0])
            t1 = pd.to_datetime(now_iso)
            elapsed_days = max(0.5, (t1 - t0).total_seconds() / 86400.0)
            ann_ret = ((total_equity / initial_capital) ** (365.0 / elapsed_days)) - 1.0
            calmar_hourly = ann_ret / max_dd if max_dd > 1e-6 else 0.0

            print("\n[Method B: Real-Time Periodic Equity Snapshots (Account Inception Oct 5 to Oct 7)]")
            print(f"  Period Elapsed:       {elapsed_days:.2f} days")
            print(f"  Current Sharpe:       {sharpe_hourly:+.2f}")
            print(f"  Current Sortino:      {sortino_hourly:+.2f}")
            print(f"  Current Calmar:       {calmar_hourly:+.2f}")
            print(f"  Current Max Drawdown: {max_dd * 100:.2f}% (Limit: < 4.00%)")
            print(f"  Composite Score:      {(0.4 * sortino_hourly) + (0.3 * sharpe_hourly) + (0.3 * calmar_hourly):+.2f}")

    print("=====================================================================")

if __name__ == "__main__":
    main()
