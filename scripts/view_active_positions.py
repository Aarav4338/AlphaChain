"""
AlphaChain Real-Time Positions, Active PnL & Risk Ratios Monitor.
Fetches live holdings, margin shorts, entry prices, current valuations,
and portfolio risk ratios directly from Roostoo mock exchange server.

Usage:
  python scripts/view_active_positions.py          # Single instant snapshot
  python scripts/view_active_positions.py --watch  # Auto-refreshing live dashboard (every 5s)
"""
import sys
import os
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from client.roostoo_client import RoostooClient
from config.trading_config import trading_config


def clear_terminal():
    os.system("cls" if os.name == "nt" else "clear")


def format_price(p: float) -> str:
    if p <= 0:
        return "$0.00"
    if p < 0.0001:
        return f"${p:.8f}"
    if p < 1.0:
        return f"${p:.4f}"
    return f"${p:,.4f}"


def fetch_and_display():
    client = RoostooClient()

    # 1. Fetch live balance, tickers, shorts, and matched orders
    bal_res = client.get_balance()
    if not bal_res or not bal_res.get("Success", False):
        print(f"[ERROR] Failed to fetch balance from Roostoo API: {bal_res.get('ErrMsg', 'Unknown error')}")
        return

    ticker_res = client.get_ticker()
    tickers: Dict[str, Dict[str, Any]] = ticker_res.get("Data", {}) if ticker_res and ticker_res.get("Success", False) else {}

    short_res = client.get_short_positions()
    raw_shorts: List[Dict[str, Any]] = short_res.get("Positions", []) if short_res and short_res.get("Success", False) else []

    order_res = client.query_order()
    orders: List[Dict[str, Any]] = order_res.get("OrderMatched", []) if order_res and order_res.get("Success", False) else []

    # Map latest filled BUY price per pair for spot longs
    buy_entry_map: Dict[str, float] = {}
    for o in orders:
        if o.get("Side") == "BUY" and o.get("Status") == "FILLED":
            pair = o.get("Pair")
            if pair and pair not in buy_entry_map:
                price = float(o.get("FilledAverPrice") or o.get("Price") or 0.0)
                if price > 0:
                    buy_entry_map[pair] = price

    # 2. Extract Cash and Spot Longs
    wallet = bal_res.get("SpotWallet") or bal_res.get("Wallet", {})
    usd_info = wallet.get("USD", {})
    free_usd = float(usd_info.get("Free", 0.0))
    locked_usd = float(usd_info.get("Lock", 0.0))

    long_positions: List[Dict[str, Any]] = []
    total_long_value = 0.0
    total_long_pnl = 0.0

    for coin, info in wallet.items():
        if coin == "USD":
            continue
        free_q = float(info.get("Free", 0.0))
        lock_q = float(info.get("Lock", 0.0))
        qty = free_q + lock_q
        if qty <= 0:
            continue

        pair = f"{coin}/USD"
        curr_p = float(tickers.get(pair, {}).get("LastPrice", 0.0))
        entry_p = buy_entry_map.get(pair, curr_p)
        val = qty * curr_p

        # Ignore tiny fractional rounding dust under $10
        if val < 10.0:
            continue

        pnl_usd = (curr_p - entry_p) * qty if entry_p > 0 else 0.0
        pnl_pct = ((curr_p / entry_p) - 1.0) * 100.0 if entry_p > 0 else 0.0

        total_long_value += val
        total_long_pnl += pnl_usd

        long_positions.append({
            "pair": pair,
            "coin": coin,
            "quantity": qty,
            "entry_price": entry_p,
            "current_price": curr_p,
            "value_usd": val,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        })

    # Sort longs by value descending
    long_positions.sort(key=lambda x: x["value_usd"], reverse=True)

    # 3. Extract Margin Shorts
    short_positions: List[Dict[str, Any]] = []
    total_short_collateral = 0.0
    total_short_pnl = 0.0

    for s in raw_shorts:
        pair = s.get("Pair", "N/A")
        qty = float(s.get("ShortQty", 0.0))
        collateral = float(s.get("Collateral", 0.0))
        entry_p = float(s.get("EntryPrice", 0.0))
        curr_p = float(s.get("CurrentPrice") or tickers.get(pair, {}).get("LastPrice") or entry_p)
        pnl_usd = float(s.get("UnrealizedPNL", 0.0))
        if pnl_usd == 0.0 and entry_p > 0:
            pnl_usd = (entry_p - curr_p) * qty
        pnl_pct = (pnl_usd / collateral * 100.0) if collateral > 0 else 0.0

        total_short_collateral += collateral
        total_short_pnl += pnl_usd

        short_positions.append({
            "pair": pair,
            "quantity": qty,
            "collateral": collateral,
            "entry_price": entry_p,
            "current_price": curr_p,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        })

    # Sort shorts by collateral descending
    short_positions.sort(key=lambda x: x["collateral"], reverse=True)

    # 4. Accurate Portfolio Valuation (Unallocated Lock guard)
    unallocated_lock = max(0.0, locked_usd - total_short_collateral)
    shorts_active_equity = max(0.0, total_short_collateral + total_short_pnl)
    total_equity = free_usd + unallocated_lock + total_long_value + shorts_active_equity

    start_cap = trading_config.INITIAL_PORTFOLIO_USD
    cum_pnl_usd = total_equity - start_cap
    cum_return_pct = (cum_pnl_usd / start_cap) * 100.0
    drawdown_pct = max(0.0, (start_cap - total_equity) / start_cap * 100.0)

    total_unrealized_pnl = total_long_pnl + total_short_pnl
    gross_exposure = total_long_value + total_short_collateral
    net_exposure = total_long_value - total_short_collateral
    long_weight_pct = (total_long_value / total_equity * 100.0) if total_equity > 0 else 0.0
    short_weight_pct = (total_short_collateral / total_equity * 100.0) if total_equity > 0 else 0.0
    cash_ratio_pct = (free_usd / total_equity * 100.0) if total_equity > 0 else 0.0
    net_delta_pct = (net_exposure / total_equity * 100.0) if total_equity > 0 else 0.0
    ls_ratio = (total_long_value / total_short_collateral) if total_short_collateral > 0 else 0.0

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # =========================================================================
    # RENDER DASHBOARD
    # =========================================================================
    print("=" * 102)
    print(f"  ALPHACHAIN REAL-TIME POSITIONS & ACTIVE PNL MONITOR   |   {now_utc}")
    print("=" * 102)

    # [1] PORTFOLIO & RISK RATIOS
    print("\n[1] PORTFOLIO VALUATION & EXPOSURE RATIOS")
    print("-" * 102)
    print(f"  Total Account Equity: ${total_equity:>11,.2f} USD   |   Starting Capital:     ${start_cap:>11,.2f} USD")
    print(f"  Cumulative PnL:       {cum_pnl_usd:>+11,.2f} USD ({cum_return_pct:>+6.2f}%) |   Current Drawdown:      {drawdown_pct:>6.2f}% (Cliff: <4.00%)")
    print(f"  Total Active PnL:     {total_unrealized_pnl:>+11,.2f} USD           |   Free Cash Buffer:     ${free_usd:>11,.2f} USD ({cash_ratio_pct:>5.1f}%)")
    print("-" * 102)
    print(f"  Long Exposure:        ${total_long_value:>11,.2f} USD ({long_weight_pct:>5.1f}%) |   Open Longs:            {len(long_positions):>2} assets")
    print(f"  Short Exposure:       ${total_short_collateral:>11,.2f} USD ({short_weight_pct:>5.1f}%) |   Open Shorts:           {len(short_positions):>2} assets")
    print(f"  Gross Exposure:       ${gross_exposure:>11,.2f} USD ({(gross_exposure/total_equity*100.0):>5.1f}%) |   Long/Short Ratio:      {ls_ratio:>6.2f}x (Hedged)")
    print(f"  Net Delta Bias:       {net_exposure:>+11,.2f} USD ({net_delta_pct:>+5.1f}%) |   Total Active Positions:{len(long_positions) + len(short_positions):>2} positions")

    # [2] SPOT LONG POSITIONS TABLE
    print("\n[2] CURRENT OPEN LONG POSITIONS (SPOT ASSETS)")
    print("-" * 102)
    if long_positions:
        print(f"  {'PAIR':<12} {'QUANTITY':<14} {'ENTRY PRICE':<14} {'CURRENT PRICE':<14} {'POSITION VALUE':<16} {'ACTIVE PNL (USD)':<18} {'WEIGHT %':<8}")
        print("  " + "-" * 98)
        for p in long_positions:
            w_pct = (p["value_usd"] / total_equity) * 100.0
            pnl_str = f"{p['pnl_usd']:+,.2f} ({p['pnl_pct']:+,.2f}%)"
            ep_str = format_price(p['entry_price'])
            cp_str = format_price(p['current_price'])
            print(f"  {p['pair']:<12} {p['quantity']:<14,.4f} {ep_str:<14} {cp_str:<14} ${p['value_usd']:<15,.2f} {pnl_str:<18} {w_pct:>5.1f}%")
        print("  " + "-" * 98)
        tot_l_pnl_str = f"{total_long_pnl:+,.2f} USD"
        print(f"  {'TOTAL LONGS':<12} {'':<14} {'':<14} {'':<14} ${total_long_value:<15,.2f} {tot_l_pnl_str:<18} {long_weight_pct:>5.1f}%")
    else:
        print("  No active Long positions.")

    # [3] MARGIN SHORT POSITIONS TABLE
    print("\n[3] CURRENT OPEN SHORT POSITIONS (MARGIN CONTRACTS)")
    print("-" * 102)
    if short_positions:
        print(f"  {'PAIR':<12} {'QUANTITY':<14} {'ENTRY PRICE':<14} {'CURRENT PRICE':<14} {'COLLATERAL (USD)':<16} {'ACTIVE PNL (USD)':<18} {'WEIGHT %':<8}")
        print("  " + "-" * 98)
        for s in short_positions:
            w_pct = (s["collateral"] / total_equity) * 100.0
            pnl_str = f"{s['pnl_usd']:+,.2f} ({s['pnl_pct']:+,.2f}%)"
            ep_str = format_price(s['entry_price'])
            cp_str = format_price(s['current_price'])
            print(f"  {s['pair']:<12} {s['quantity']:<14,.4f} {ep_str:<14} {cp_str:<14} ${s['collateral']:<15,.2f} {pnl_str:<18} {w_pct:>5.1f}%")
        print("  " + "-" * 98)
        tot_s_pnl_str = f"{total_short_pnl:+,.2f} USD"
        print(f"  {'TOTAL SHORTS':<12} {'':<14} {'':<14} {'':<14} ${total_short_collateral:<15,.2f} {tot_s_pnl_str:<18} {short_weight_pct:>5.1f}%")
    else:
        print("  No active Short positions.")

    print("=" * 102 + "\n")


def main():
    parser = argparse.ArgumentParser(description="AlphaChain Live Positions & PnL Monitor")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh and display live positions")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5s)")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                clear_terminal()
                fetch_and_display()
                print(f"Press Ctrl+C to exit watch mode. Refreshing every {args.interval}s...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitor terminated.")
    else:
        fetch_and_display()


if __name__ == "__main__":
    main()
