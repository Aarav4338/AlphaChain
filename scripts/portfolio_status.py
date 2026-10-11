import sys, os, time
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.roostoo_client import RoostooClient
from dotenv import load_dotenv

load_dotenv()

c = RoostooClient(os.getenv("ROOSTOO_API_KEY"), os.getenv("ROOSTOO_SECRET_KEY"))

bal = c.get_balance()
spot = bal.get("SpotWallet", {})
usd_free = spot.get("USD", {}).get("Free", 0.0)
usd_lock = spot.get("USD", {}).get("Lock", 0.0)

shorts_resp = c.get_short_positions()
positions = shorts_resp.get("Positions", []) if shorts_resp else []
tickers = c.get_ticker().get("Data", {})

# Spot holdings valuation
spot_value = 0.0
for coin, coin_info in spot.items():
    if coin != "USD":
        coin_qty = float(coin_info.get("Free", 0.0)) + float(coin_info.get("Lock", 0.0))
        if coin_qty > 0:
            pair = f"{coin}/USD"
            price = float(tickers.get(pair, {}).get("LastPrice", 0.0))
            spot_value += coin_qty * price

now = time.time()
total_unrealized = sum(p.get("UnrealizedPNL", 0.0) for p in positions)
total_collateral = sum(p.get("Collateral", 0.0) for p in positions)
total_equity = usd_free + usd_lock + spot_value + total_unrealized

initial_capital = 100_000.0
total_pnl = total_equity - initial_capital
total_return_pct = (total_pnl / initial_capital) * 100.0
drawdown_from_100k = max(0.0, (initial_capital - total_equity) / initial_capital * 100.0)

print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("=" * 65)
print(f"Total Equity:         ${total_equity:,.2f}")
print(f"Initial Capital:      ${initial_capital:,.2f}")
print(f"Net PnL vs Start:     ${total_pnl:+,.2f} ({total_return_pct:+.2f}%)")
print(f"Free Cash:            ${usd_free:,.2f} ({(usd_free / total_equity) * 100:.1f}%)")
print(f"Collateral Locked:    ${usd_lock:,.2f} ({(usd_lock / total_equity) * 100:.1f}%)")
print(f"Unrealized PnL:       ${total_unrealized:+,.2f}")
print(f"Current Drawdown:     {drawdown_from_100k:.2f}% (Disqualification Cliff: 4.00%)")
print(f"Safety Margin:        {4.00 - drawdown_from_100k:.2f}% remaining before breaker")
print("=" * 65)
print(f"{'PAIR':<10} | {'ENTRY':<9} | {'CURRENT':<9} | {'COLLATERAL':<11} | {'PNL ($)':<9} | {'PNL (%)':<8} | {'AGE':<6}")
print("-" * 65)
for p in positions:
    created_ts = p.get("CreateTimestamp", 0) / 1000
    age_h = (now - created_ts) / 3600
    pair = p.get("Pair", "")
    entry = p.get("EntryPrice", 0.0)
    current = p.get("CurrentPrice", 0.0)
    collat = p.get("Collateral", 0.0)
    pnl = p.get("UnrealizedPNL", 0.0)
    pnl_pct = p.get("UnrealizedPNLPct", 0.0) * 100.0
    print(f"{pair:<10} | {entry:<9.4f} | {current:<9.4f} | ${collat:<10,.2f} | {pnl:>+8.2f} | {pnl_pct:>+6.2f}% | {age_h:.1f}h")
print("=" * 65)

