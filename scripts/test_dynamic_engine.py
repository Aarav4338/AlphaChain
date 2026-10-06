"""
Fully Dynamic Adaptive Engine Prototype & Scenario Simulator.
"""
import sys
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from strategy.signals import SignalAction, TradeSignal


class FullyDynamicPositionSizer:
    def __init__(
        self,
        max_portfolio_heat: float = 0.016,     # 1.6% max cumulative heat at peak equity
        max_risk_per_asset: float = 0.0075,    # 0.75% max risk per trade
        max_coin_allocation: float = 0.35,     # Up to 35% on single high-conviction leader
        min_cash_buffer: float = 0.15,         # 15% minimum cash reserve (up to 85% deployed)
        drawdown_limit: float = 0.040,         # 4.0% hackathon disqualification limit
    ):
        self.max_portfolio_heat = max_portfolio_heat
        self.max_risk_per_asset = max_risk_per_asset
        self.max_coin_allocation = max_coin_allocation
        self.min_cash_buffer = min_cash_buffer
        self.drawdown_limit = drawdown_limit

    def calculate_sizes(
        self,
        signals,
        portfolio_equity: float,
        free_usd: float,
        current_drawdown: float,
        open_positions: list,
        regime_adx: float = 20.0,
    ):
        if portfolio_equity <= 0 or not signals:
            return []

        # 1. Distance-to-Cliff Governor:
        # As drawdown approaches 4%, available risk heat budget scales down smoothly to 0
        dd_fraction = min(1.0, max(0.0, current_drawdown / self.drawdown_limit))
        headroom_multiplier = max(0.05, (1.0 - dd_fraction) ** 1.5)
        
        # Continuous regime volatility multiplier (ADX scaling)
        regime_mult = float(np.clip((regime_adx - 10.0) / 15.0, 0.40, 1.0))
        
        dynamic_heat_cap = self.max_portfolio_heat * headroom_multiplier * regime_mult
        
        # Calculate currently open risk heat
        open_heat = 0.0
        for p in open_positions:
            qty = getattr(p, "quantity", 0.0)
            entry_p = getattr(p, "entry_price", 0.0)
            stop_p = getattr(p, "stop_loss", 0.0)
            side = getattr(p, "side", SignalAction.BUY)
            if side == SignalAction.BUY and entry_p > stop_p:
                open_heat += (qty * (entry_p - stop_p)) / portfolio_equity

        available_heat = max(0.0, dynamic_heat_cap - open_heat)
        available_cash = max(0.0, free_usd - (portfolio_equity * self.min_cash_buffer))

        if available_heat < 0.0005 or available_cash < 100.0:
            return []

        # 2. Filter valid signals
        valid_sigs = [s for s in signals if s.conviction >= 0.30 and abs(s.entry_price - s.stop_loss_price) > 0]
        if not valid_sigs:
            return []

        total_conv = sum(s.conviction for s in valid_sigs)
        total_assets = len(open_positions) + len(valid_sigs)

        # 3. Dynamic conviction-weighted sizing
        orders = []
        for sig in valid_sigs:
            w = sig.conviction / total_conv
            target_risk = portfolio_equity * min(self.max_risk_per_asset, available_heat * w)
            stop_dist = abs(sig.entry_price - sig.stop_loss_price)
            raw_qty = target_risk / stop_dist
            capital = raw_qty * sig.entry_price

            # Elastic per-coin ceiling: expands up to 35% when few assets qualify
            per_coin_cap_pct = min(self.max_coin_allocation, (1.0 - self.min_cash_buffer) / max(1, total_assets) * 1.5)
            max_capital = portfolio_equity * per_coin_cap_pct

            if capital > max_capital:
                capital = max_capital
                raw_qty = capital / sig.entry_price
                
            orders.append({
                "sig": sig,
                "qty": raw_qty,
                "capital": capital,
                "target_risk": target_risk,
            })

        # 4. Proportional cash scaling if sum exceeds available cash
        tot_cap = sum(o["capital"] for o in orders)
        if tot_cap > available_cash and tot_cap > 0:
            scale = available_cash / tot_cap
            for o in orders:
                o["qty"] *= scale
                o["capital"] *= scale

        return orders


def run_scenarios():
    sizer = FullyDynamicPositionSizer()

    print("=" * 80)
    print("SCENARIO 1: Peak Equity ($100k, 0.0% Drawdown), 1 High-Conviction Leader (BTC)")
    print("=" * 80)
    sig_btc = TradeSignal(pair="BTC/USD", action=SignalAction.BUY, conviction=0.90, entry_price=85000, stop_loss_price=83300, take_profit_price=88400)
    orders = sizer.calculate_sizes([sig_btc], portfolio_equity=100000, free_usd=100000, current_drawdown=0.0, open_positions=[], regime_adx=25.0)
    for o in orders:
        print(f"  {o['sig'].pair}: Capital=${o['capital']:,.2f} ({o['capital']/1000:.1f}%), Stop Risk=${o['target_risk']:,.2f} ({o['target_risk']/1000:.2f}%)")

    print("\n" + "=" * 80)
    print("SCENARIO 2: Peak Equity ($100k, 0.0% Drawdown), Broad Momentum (4 Coins Qualify)")
    print("=" * 80)
    sigs = [
        TradeSignal(pair="ETH/USD", action=SignalAction.BUY, conviction=0.85, entry_price=2700, stop_loss_price=2630, take_profit_price=2840),
        TradeSignal(pair="SOL/USD", action=SignalAction.BUY, conviction=0.80, entry_price=120, stop_loss_price=116, take_profit_price=128),
        TradeSignal(pair="XRP/USD", action=SignalAction.BUY, conviction=0.75, entry_price=1.50, stop_loss_price=1.45, take_profit_price=1.60),
        TradeSignal(pair="NEAR/USD", action=SignalAction.BUY, conviction=0.70, entry_price=5.00, stop_loss_price=4.85, take_profit_price=5.30),
    ]
    orders = sizer.calculate_sizes(sigs, portfolio_equity=100000, free_usd=100000, current_drawdown=0.0, open_positions=[], regime_adx=25.0)
    tot_cap = sum(o["capital"] for o in orders)
    tot_risk = sum(o["target_risk"] for o in orders)
    for o in orders:
        print(f"  {o['sig'].pair}: Capital=${o['capital']:,.2f} ({o['capital']/1000:.1f}%), Stop Risk=${o['target_risk']:,.2f} ({o['target_risk']/1000:.2f}%)")
    print(f"  >>> TOTAL DEPLOYED: ${tot_cap:,.2f} ({tot_cap/1000:.1f}%) | TOTAL STOP HEAT: ${tot_risk:,.2f} ({tot_risk/1000:.2f}%)")

    print("\n" + "=" * 80)
    print("SCENARIO 3: Account in 2.5% Drawdown (Approaching the 4.0% Cliff)")
    print("=" * 80)
    orders_dd = sizer.calculate_sizes(sigs, portfolio_equity=97500, free_usd=97500, current_drawdown=0.025, open_positions=[], regime_adx=25.0)
    tot_cap_dd = sum(o["capital"] for o in orders_dd)
    tot_risk_dd = sum(o["target_risk"] for o in orders_dd)
    for o in orders_dd:
        print(f"  {o['sig'].pair}: Capital=${o['capital']:,.2f} ({o['capital']/975:.1f}%), Stop Risk=${o['target_risk']:,.2f} ({o['target_risk']/975:.2f}%)")
    print(f"  >>> TOTAL DEPLOYED IN DD: ${tot_cap_dd:,.2f} ({tot_cap_dd/975:.1f}%) | TOTAL STOP HEAT: ${tot_risk_dd:,.2f} ({tot_risk_dd/975:.2f}%)")


if __name__ == "__main__":
    run_scenarios()
