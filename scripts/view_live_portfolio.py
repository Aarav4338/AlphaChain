"""
Live Portfolio, Order Audit & Trade History Terminal Viewer.
Directly connects to Roostoo Exchange API and AWS EC2 state to provide
real-time visibility into portfolio equity, open positions, asset valuations,
and complete executed order history.
"""
import sys
import datetime
from pathlib import Path
from typing import Dict, Any, List

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.roostoo_client import RoostooClient
from config.trading_config import trading_config


def format_ts(ms_timestamp) -> str:
    """Format millisecond epoch to readable UTC datetime."""
    try:
        dt = datetime.datetime.fromtimestamp(int(ms_timestamp) / 1000.0, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ms_timestamp)


def view_live_portfolio():
    client = RoostooClient()

    print("=" * 95)
    print("  ROOSTOO QUANT HACKATHON: LIVE PORTFOLIO & TRADE AUDIT MONITOR")
    print("=" * 95)

    # 1. Fetch Balances
    bal_res = client.get_balance()
    if not bal_res or not bal_res.get("Success", False):
        print(f"Error fetching balance from Roostoo API: {bal_res}")
        return

    spot_wallet = bal_res.get("SpotWallet", {})
    margin_wallet = bal_res.get("MarginWallet", {})

    # 2. Fetch Real-time Market Prices
    tickers_res = client.get_ticker()
    prices: Dict[str, float] = {}
    if tickers_res and tickers_res.get("Success", False):
        data = tickers_res.get("Data", {})
        for pair, tdata in data.items():
            try:
                prices[pair] = float(tdata.get("LastPrice", 0.0))
            except Exception:
                pass

    # 3. Compute Portfolio Equity & Valuation
    usd_free = float(spot_wallet.get("USD", {}).get("Free", 0.0))
    usd_lock = float(spot_wallet.get("USD", {}).get("Lock", 0.0))
    total_cash_usd = usd_free + usd_lock

    positions_value_usd = 0.0
    coin_holdings: List[Dict[str, Any]] = []

    for coin, info in spot_wallet.items():
        if coin == "USD":
            continue
        free_qty = float(info.get("Free", 0.0))
        lock_qty = float(info.get("Lock", 0.0))
        total_qty = free_qty + lock_qty

        if total_qty > 0:
            pair = f"{coin}/USD"
            curr_p = prices.get(pair, 0.0)
            val_usd = total_qty * curr_p
            positions_value_usd += val_usd
            coin_holdings.append({
                "coin": coin,
                "pair": pair,
                "free_qty": free_qty,
                "lock_qty": lock_qty,
                "total_qty": total_qty,
                "current_price": curr_p,
                "value_usd": val_usd,
            })

    total_equity = total_cash_usd + positions_value_usd
    starting_capital = trading_config.INITIAL_PORTFOLIO_USD
    cum_pnl_usd = total_equity - starting_capital
    cum_return_pct = (cum_pnl_usd / starting_capital) * 100.0
    drawdown_pct = max(0.0, (starting_capital - total_equity) / starting_capital * 100.0)

    # 4. Display Account Summary
    print("\n[1] PORTFOLIO VALUATION SUMMARY")
    print("-" * 95)
    print(f"  Starting Capital:      ${starting_capital:,.2f} USD")
    print(f"  Current Total Equity:  ${total_equity:,.2f} USD")
    print(f"  Cumulative PnL:        {cum_pnl_usd:+,.2f} USD ({cum_return_pct:+.2f}%)")
    print(f"  Current Drawdown:      {drawdown_pct:.2f}% (Disqualification Cliff: < 4.00%)")
    print(f"  Cash In Reserve:       ${total_cash_usd:,.2f} USD ({(total_cash_usd/total_equity*100.0):.1f}% of equity)")
    print(f"  Active Coin Exposure:  ${positions_value_usd:,.2f} USD ({(positions_value_usd/total_equity*100.0):.1f}% of equity)")

    # 5. Display Active Asset Holdings
    print("\n[2] CURRENT OPEN ASSET HOLDINGS")
    print("-" * 95)
    if coin_holdings:
        print(f"  {'COIN':<8} {'FREE QTY':<14} {'LOCKED':<10} {'PRICE (USD)':<14} {'MARKET VALUE':<16} {'WEIGHT %':<10}")
        print("  " + "-" * 85)
        for h in coin_holdings:
            weight = (h["value_usd"] / total_equity) * 100.0
            print(f"  {h['coin']:<8} {h['free_qty']:<14,.4f} {h['lock_qty']:<10,.4f} ${h['current_price']:<13,.4f} ${h['value_usd']:<15,.2f} {weight:>5.1f}%")
    else:
        print("  No open spot positions. Portfolio is 100% in USD cash.")

    # 6. Fetch & Display Short Positions
    short_res = client.get_short_positions()
    shorts = short_res.get("Positions", []) if short_res and short_res.get("Success", False) else []
    if shorts:
        print("\n[3] ACTIVE MARGIN SHORT POSITIONS")
        print("-" * 95)
        print(f"  {'PAIR':<10} {'QTY':<12} {'COLLATERAL':<14} {'ENTRY PRICE':<14} {'CURRENT PRICE':<14} {'UNREALIZED PNL':<15}")
        print("  " + "-" * 85)
        for s in shorts:
            pair = s.get("Pair") or s.get("pair") or "N/A"
            qty = float(s.get("ShortQty") or s.get("quantity") or 0.0)
            collateral = float(s.get("Collateral") or s.get("collateral") or 0.0)
            entry_p = float(s.get("EntryPrice") or s.get("entry_price") or 0.0)
            curr_p = float(s.get("CurrentPrice") or s.get("current_price") or entry_p)
            unrealized = float(s.get("UnrealizedPNL") or s.get("unrealized_pnl") or 0.0)
            print(f"  {pair:<10} {qty:<12,.4f} ${collateral:<13,.2f} ${entry_p:<13,.4f} ${curr_p:<13,.4f} {unrealized:+,.2f} USD")

    # 7. Fetch & Display Complete Trade / Order History
    print("\n[4] EXECUTED ORDERS & AUDIT TRAIL (ROOSTOO SERVER)")
    print("-" * 95)
    orders_res = client.query_order()
    orders = orders_res.get("OrderMatched", []) if orders_res and orders_res.get("Success", False) else []

    if orders:
        # Sort chronologically descending
        orders.sort(key=lambda o: o.get("CreateTimestamp", 0), reverse=True)
        print(f"  {'ORDER ID':<10} {'TIME (UTC)':<22} {'PAIR':<10} {'SIDE':<6} {'STATUS':<10} {'EXEC PRICE':<12} {'QTY':<12} {'VALUE (USD)':<14} {'FEE (USD)':<10}")
        print("  " + "-" * 108)
        for o in orders:
            oid = str(o.get("OrderID", "N/A"))
            ts_str = format_ts(o.get("CreateTimestamp", 0))
            pair = o.get("Pair", "N/A")
            side = o.get("Side", "N/A")
            status = o.get("Status", "N/A")
            price = float(o.get("FilledAverPrice", 0.0) or o.get("Price", 0.0))
            qty = float(o.get("FilledQuantity", 0.0) or o.get("Quantity", 0.0))
            val_usd = float(o.get("UnitChange", 0.0)) if o.get("UnitChange") else (price * qty)
            fee = float(o.get("CommissionChargeValue", 0.0))
            role = o.get("Role", "MAKER")

            print(f"  {oid:<10} {ts_str:<22} {pair:<10} {side:<6} {status:<10} ${price:<11.4f} {qty:<12,.4f} ${val_usd:<13,.2f} ${fee:<9.4f}")
    else:
        print("  No executed orders returned by Roostoo server yet.")

    # 8. AWS Production Daemon Heartbeat State
    print("\n[5] AWS EC2 PRODUCTION HOST STATE")
    print("-" * 95)
    deploy_state_file = PROJECT_ROOT / "deploy" / "deployment_state.json"
    if deploy_state_file.exists():
        import json
        with open(deploy_state_file, "r", encoding="utf-8") as f:
            dstate = json.load(f)
        print(f"  Instance ID:      {dstate.get('instance_id')}")
        print(f"  Public IP:        {dstate.get('public_ip')}")
        print(f"  Availability Zone:{dstate.get('availability_zone')}")
        print(f"  Active Branch:    {dstate.get('branch')}")
        print(f"  Active Profile:   {dstate.get('profile')}")
        print(f"  Daemon Launch:    {dstate.get('launch_time')}")
    else:
        print("  Deployment state file not found locally.")

    print("=" * 95 + "\n")


if __name__ == "__main__":
    view_live_portfolio()
