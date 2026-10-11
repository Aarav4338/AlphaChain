import sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.roostoo_client import RoostooClient
from config.trading_config import TradingConfig
from dotenv import load_dotenv

load_dotenv()

c = RoostooClient(os.getenv("ROOSTOO_API_KEY"), os.getenv("ROOSTOO_SECRET_KEY"))
cfg = TradingConfig()

all_orders = []
for pair in cfg.ASSET_UNIVERSE:
    try:
        resp = c.query_order(pair=pair)
        for o in resp.get("OrderMatched", []):
            o["_pair"] = pair
            all_orders.append(o)
    except Exception:
        pass

all_orders.sort(key=lambda x: x.get("CreateTimestamp", 0))

print(f"Total matched orders in system: {len(all_orders)}")

# Pair trades by symbol
trades_by_pair = {}
total_commission = 0.0

for o in all_orders:
    p = o["_pair"]
    side = o.get("Side")
    qty = float(o.get("FilledQuantity", 0))
    price = float(o.get("FilledAverPrice", 0))
    ts = o.get("CreateTimestamp", 0) / 1000
    comm = float(o.get("CommissionChargeValue", 0))
    total_commission += comm
    
    if p not in trades_by_pair:
        trades_by_pair[p] = []
    trades_by_pair[p].append({
        "ts": ts,
        "dt": datetime.fromtimestamp(ts, tz=timezone.utc),
        "side": side,
        "qty": qty,
        "price": price,
        "comm": comm,
    })

print(f"Total commissions paid: ${total_commission:,.2f}")
print()
print("=" * 80)
print("RECENT CLOSED TRADE HISTORY (Oct 7 - Oct 8):")
print("=" * 80)

# Track short trade completions: SHORT_OPEN followed by SHORT_CLOSE
# or BUY followed by SELL
closed_trades = []

for pair, order_list in trades_by_pair.items():
    # Sort orders chronologically
    order_list.sort(key=lambda x: x["ts"])
    
    # Process short trades
    open_shorts = []
    for o in order_list:
        if o["side"] == "SHORT_OPEN":
            open_shorts.append(o)
        elif o["side"] == "SHORT_CLOSE":
            # Match against open shorts
            close_qty = o["qty"]
            close_price = o["price"]
            close_dt = o["dt"]
            
            while close_qty > 1e-6 and open_shorts:
                entry_o = open_shorts[0]
                matched_qty = min(close_qty, entry_o["qty"])
                entry_price = entry_o["price"]
                entry_dt = entry_o["dt"]
                
                # Short PnL = (Entry - Exit) * qty - commissions
                trade_pnl = (entry_price - close_price) * matched_qty
                pnl_pct = (entry_price - close_price) / entry_price * 100.0
                duration_h = (o["ts"] - entry_o["ts"]) / 3600.0
                
                closed_trades.append({
                    "pair": pair,
                    "type": "SHORT",
                    "entry_dt": entry_dt,
                    "exit_dt": close_dt,
                    "qty": matched_qty,
                    "entry_price": entry_price,
                    "exit_price": close_price,
                    "pnl": trade_pnl,
                    "pnl_pct": pnl_pct,
                    "duration_h": duration_h,
                })
                
                entry_o["qty"] -= matched_qty
                close_qty -= matched_qty
                if entry_o["qty"] <= 1e-6:
                    open_shorts.pop(0)

        # Process spot long trades
    open_longs = []
    for o in order_list:
        if o["side"] == "BUY":
            open_longs.append(o)
        elif o["side"] == "SELL":
            sell_qty = o["qty"]
            sell_price = o["price"]
            sell_dt = o["dt"]
            while sell_qty > 1e-6 and open_longs:
                entry_o = open_longs[0]
                matched_qty = min(sell_qty, entry_o["qty"])
                entry_price = entry_o["price"]
                trade_pnl = (sell_price - entry_price) * matched_qty
                pnl_pct = (sell_price - entry_price) / max(entry_price, 1e-6) * 100.0
                duration_h = (o["ts"] - entry_o["ts"]) / 3600.0
                
                closed_trades.append({
                    "pair": pair,
                    "type": "LONG",
                    "entry_dt": entry_o["dt"],
                    "exit_dt": sell_dt,
                    "qty": matched_qty,
                    "entry_price": entry_price,
                    "exit_price": sell_price,
                    "pnl": trade_pnl,
                    "pnl_pct": pnl_pct,
                    "duration_h": duration_h,
                })
                entry_o["qty"] -= matched_qty
                sell_qty -= matched_qty
                if entry_o["qty"] <= 1e-6:
                    open_longs.pop(0)

# Sort closed trades by exit time
closed_trades.sort(key=lambda x: x["exit_dt"])

total_realized_pnl = sum(t["pnl"] for t in closed_trades)
winners = [t for t in closed_trades if t["pnl"] > 0]
losers = [t for t in closed_trades if t["pnl"] <= 0]
win_rate = (len(winners) / len(closed_trades) * 100) if closed_trades else 0

for t in closed_trades[-20:]:
    exit_str = t["exit_dt"].strftime("%m-%d %H:%M")
    print(f"{exit_str} | {t['pair']:<10} | {t['type']:<5} | Entry: {t['entry_price']:<9.4f} -> Exit: {t['exit_price']:<9.4f} | PnL: {t['pnl']:>+8.2f} ({t['pnl_pct']:>+5.2f}%) | {t['duration_h']:.1f}h")

print("=" * 80)
print(f"Total Closed Trades:   {len(closed_trades)}")
print(f"Winning Trades:        {len(winners)} ({win_rate:.1f}%)")
print(f"Losing Trades:         {len(losers)} ({100 - win_rate:.1f}%)")
print(f"Gross Realized PnL:    ${total_realized_pnl:+,.2f}")
print(f"Total Commissions:     ${total_commission:,.2f}")
print(f"Net Realized PnL:      ${total_realized_pnl - total_commission:+,.2f}")
print("=" * 80)

