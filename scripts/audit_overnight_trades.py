import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.roostoo_client import RoostooClient

def main():
    client = RoostooClient()
    res = client.query_order(pending_only=False)
    orders = res.get("OrderMatched", []) if res else []
    orders.sort(key=lambda x: x.get("FinishTimestamp") or x.get("CreateTimestamp") or 0, reverse=True)

    print(f"Total historical orders on Roostoo: {len(orders)}")
    print("=" * 80)
    print("MOST RECENT 25 FILLED ORDERS (SINCE REDEPLOYMENT)")
    print("=" * 80)
    for i, o in enumerate(orders[:25]):
        ts = (o.get("FinishTimestamp") or o.get("CreateTimestamp") or 0) / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        pair = o.get("Pair")
        side = o.get("Side")
        status = o.get("Status")
        price = float(o.get("FilledAverPrice", 0.0))
        qty = float(o.get("FilledQuantity", 0.0))
        comm = float(o.get("CommissionChargeValue", 0.0))
        notional = price * qty
        print(f"[{dt}] {side:<12} {pair:<10} Status: {status:<8} Price: ${price:<10.4f} Qty: {qty:<10.4f} Val: ${notional:,.2f} Fee: ${comm:.2f}")

if __name__ == "__main__":
    main()

