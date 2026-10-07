"""
Close all 26 existing flat hedged positions on Roostoo mock exchange.
Banks the net profit into pure USD cash to allow clean rebalance into 8 concentrated top movers.
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.roostoo_client import RoostooClient

def main():
    client = RoostooClient()

    print("=====================================================================")
    print("        CLOSING 26 FLAT HEDGED POSITIONS TO UNLOCK CASH             ")
    print("=====================================================================")

    # 1. Close Margin Shorts
    shorts_res = client.get_short_positions() or {}
    raw_shorts = shorts_res if isinstance(shorts_res, list) else shorts_res.get("Positions", [])
    print(f"\n[1] Closing {len(raw_shorts)} Margin Shorts:")
    closed_shorts_count = 0
    total_short_pnl = 0.0

    for s in raw_shorts:
        pair = s.get("Pair")
        qty = float(s.get("ShortQty", 0.0))
        if qty > 0 and pair:
            print(f"  Closing SHORT {pair} (Qty: {qty})...", end=" ")
            res = client.short_close(pair=pair, close_qty=qty)
            success = res.get("Success", False) if res else False
            pnl = float(res.get("RealizedPNL", 0.0)) if res else 0.0
            total_short_pnl += pnl
            print(f"-> Success: {success} | Realized PnL: ${pnl:+.2f}")
            closed_shorts_count += 1
            time.sleep(0.3)

    # 2. Sell Spot Longs
    bal = client.get_balance() or {}
    wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
    print(f"\n[2] Selling Spot Longs:")
    closed_longs_count = 0

    for coin, info in wallet.items():
        if coin == "USD":
            continue
        free_q = float(info.get("Free", 0.0))
        pair = f"{coin}/USD"
        if free_q > 0.00001:
            print(f"  Selling SPOT {pair} (Qty: {free_q})...", end=" ")
            res = client.place_order(pair=pair, side="SELL", quantity=free_q, order_type="MARKET")
            success = res.get("Success", False) if res else False
            print(f"-> Success: {success}")
            closed_longs_count += 1
            time.sleep(0.3)

    print("\n---------------------------------------------------------------------")
    print(f"Closed {closed_shorts_count} Shorts | Sold {closed_longs_count} Longs")
    print(f"Realized PnL from closed shorts: ${total_short_pnl:+.2f} USD")

    # 3. Final Audit
    time.sleep(2)
    final_bal = client.get_balance() or {}
    final_wallet = final_bal.get("SpotWallet") or final_bal.get("Wallet", {})
    usd_free = float(final_wallet.get("USD", {}).get("Free", 0.0))
    usd_lock = float(final_wallet.get("USD", {}).get("Lock", 0.0))
    print(f"\nFinal Available Cash: ${usd_free:,.2f} USD (Locked: ${usd_lock:,.2f} USD)")
    print("=====================================================================")

if __name__ == "__main__":
    main()

