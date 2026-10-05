"""
Dynamic Trailing Stop-Loss & Take-Profit Manager.
Continuously ratchets stops higher (for Longs) or lower (for Shorts) as trades become profitable.
Guarantees asymmetric upside while cutting losers early to protect the Sortino & Sharpe ratios.
"""
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional

from config.trading_config import trading_config
from strategy.signals import SignalAction

logger = logging.getLogger("AlphaChain.StopLossManager")


@dataclass
class TrackedPosition:
    pair: str
    side: SignalAction          # BUY (Long) or SHORT_OPEN (Short)
    entry_price: float
    current_price: float
    quantity: float             # Coin quantity
    collateral: float           # USD collateral for shorts
    stop_loss: float
    take_profit: float
    atr: float
    highest_price: float
    lowest_price: float
    trailing_activated: bool = False
    breakeven_activated: bool = False
    entry_bar: int = 0


@dataclass
class ExitSignal:
    pair: str
    action: SignalAction        # SELL (for Long) or SHORT_CLOSE (for Short)
    quantity: float
    collateral: float
    order_type: str             # "MARKET" for stops, "LIMIT" for profit targets
    reason: str


class StopLossManager:
    """Tracks active positions and enforces dynamic trailing stops and profit targets."""

    def __init__(self):
        self.positions: Dict[str, TrackedPosition] = {}

    def register_position(
        self,
        pair: str,
        side: SignalAction,
        entry_price: float,
        quantity: float,
        collateral: float,
        stop_loss: float,
        take_profit: float,
        atr: float,
        entry_bar: int = 0,
    ) -> None:
        """Register a newly opened position for tracking."""
        self.positions[pair] = TrackedPosition(
            pair=pair,
            side=side,
            entry_price=entry_price,
            current_price=entry_price,
            quantity=quantity,
            collateral=collateral,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            highest_price=entry_price,
            lowest_price=entry_price,
            trailing_activated=False,
            breakeven_activated=False,
            entry_bar=entry_bar,
        )
        logger.info(
            f"Tracking new position: {pair} ({side.value}) @ {entry_price:.4f} | "
            f"Stop: {stop_loss:.4f} | Target: {take_profit:.4f} | ATR: {atr:.4f}"
        )

    def remove_position(self, pair: str) -> Optional[TrackedPosition]:
        """Remove a closed position from tracking."""
        return self.positions.pop(pair, None)

    def close_position(self, pair: str) -> Optional[TrackedPosition]:
        """Alias for remove_position."""
        return self.remove_position(pair)

    def update_price(
        self, pair: str, current_price: float, current_bar: Optional[int] = None
    ) -> Optional[ExitSignal]:
        """
        Update the current market price for an open position.
        Enforces Triple Barrier rules:
        1. Time-Horizon Barrier (holding limit)
        2. Breakeven Ratchet (locks in zero-risk once profit reaches 1.2x ATR)
        3. Wide Dynamic Trailing Stop (allows profitable runners to expand)
        4. Take-Profit Target Hit
        """
        pos = self.positions.get(pair)
        if pos is None:
            return None

        pos.current_price = current_price

        # ----------------------------------------------------------------------
        # 1. TIME-HORIZON BARRIER (Max Holding Duration Check)
        # ----------------------------------------------------------------------
        max_bars = getattr(trading_config, "MAX_HOLDING_BARS", 0)
        if max_bars > 0 and current_bar is not None and pos.entry_bar > 0:
            holding_bars = current_bar - pos.entry_bar
            if holding_bars >= max_bars:
                action = SignalAction.SELL if pos.side == SignalAction.BUY else SignalAction.SHORT_CLOSE
                logger.info(f"⏳ Time Horizon Barrier reached for {pair} after {holding_bars} bars.")
                return ExitSignal(
                    pair=pair,
                    action=action,
                    quantity=pos.quantity,
                    collateral=pos.collateral if pos.side == SignalAction.SHORT_OPEN else 0.0,
                    order_type="MARKET",
                    reason=f"Time Horizon Barrier ({holding_bars} bars)",
                )

        # ----------------------------------------------------------------------
        # 2. LONG POSITION LOGIC
        # ----------------------------------------------------------------------
        safe_atr = max(pos.atr, 1e-6)
        if pos.side == SignalAction.BUY:
            pos.highest_price = max(pos.highest_price, current_price)
            profit_atr = (pos.highest_price - pos.entry_price) / safe_atr

            # Barrier A: Breakeven Ratchet (+1.2x ATR profit)
            be_mult = getattr(trading_config, "BREAKEVEN_ACTIVATION_ATR_MULT", 1.2)
            if profit_atr >= be_mult and not pos.breakeven_activated:
                be_stop = pos.entry_price * 1.001  # +10 bps to cover roundtrip friction
                if be_stop > pos.stop_loss:
                    pos.stop_loss = be_stop
                    pos.breakeven_activated = True
                    logger.debug(f"Breakeven stop ratcheted for {pair}: {pos.stop_loss:.4f}")

            # Barrier B: Dynamic Trailing Stop (+2.0x ATR profit)
            trailing_act = getattr(trading_config, "TRAILING_ACTIVATION_ATR_MULT", 2.0)
            trailing_dist_mult = getattr(trading_config, "TRAILING_DISTANCE_ATR_MULT", 1.8)
            if profit_atr >= trailing_act:
                pos.trailing_activated = True
                new_trailing_stop = pos.highest_price - (trailing_dist_mult * safe_atr)
                if new_trailing_stop > pos.stop_loss:
                    pos.stop_loss = new_trailing_stop
                    logger.debug(f"Trailing stop ratcheted UP for {pair}: {pos.stop_loss:.4f}")

            # Check Stop Loss trigger (MARKET order for emergency capital preservation)
            if current_price <= pos.stop_loss:
                if pos.trailing_activated:
                    reason = "Trailing Stop Loss Hit"
                elif pos.breakeven_activated:
                    reason = "Breakeven Stop Hit"
                else:
                    reason = "Initial Stop Loss Hit"
                logger.warning(f"🚨 {reason} for {pair} at {current_price:.4f} (Stop: {pos.stop_loss:.4f})")
                return ExitSignal(
                    pair=pair,
                    action=SignalAction.SELL,
                    quantity=pos.quantity,
                    collateral=0.0,
                    order_type="MARKET",
                    reason=reason,
                )

            # Check Take Profit target trigger (LIMIT order to capture 0.05% maker fee)
            if current_price >= pos.take_profit:
                logger.info(f"🎯 Take Profit target reached for {pair} at {current_price:.4f}")
                return ExitSignal(
                    pair=pair,
                    action=SignalAction.SELL,
                    quantity=pos.quantity,
                    collateral=0.0,
                    order_type="LIMIT",
                    reason="Take Profit Target Hit",
                )

        # ----------------------------------------------------------------------
        # 3. SHORT POSITION LOGIC
        # ----------------------------------------------------------------------
        elif pos.side == SignalAction.SHORT_OPEN:
            pos.lowest_price = min(pos.lowest_price, current_price)
            profit_atr = (pos.entry_price - pos.lowest_price) / safe_atr

            # Barrier A: Breakeven Ratchet (+1.2x ATR profit)
            be_mult = getattr(trading_config, "BREAKEVEN_ACTIVATION_ATR_MULT", 1.2)
            if profit_atr >= be_mult and not pos.breakeven_activated:
                be_stop = pos.entry_price * 0.999  # -10 bps to cover roundtrip friction
                if be_stop < pos.stop_loss:
                    pos.stop_loss = be_stop
                    pos.breakeven_activated = True
                    logger.debug(f"Breakeven stop ratcheted DOWN for {pair} short: {pos.stop_loss:.4f}")

            # Barrier B: Dynamic Trailing Stop (+2.0x ATR profit)
            trailing_act = getattr(trading_config, "TRAILING_ACTIVATION_ATR_MULT", 2.0)
            trailing_dist_mult = getattr(trading_config, "TRAILING_DISTANCE_ATR_MULT", 1.8)
            if profit_atr >= trailing_act:
                pos.trailing_activated = True
                new_trailing_stop = pos.lowest_price + (trailing_dist_mult * safe_atr)
                if new_trailing_stop < pos.stop_loss:
                    pos.stop_loss = new_trailing_stop
                    logger.debug(f"Trailing stop ratcheted DOWN for {pair} short: {pos.stop_loss:.4f}")

            # Check Stop Loss trigger for short
            if current_price >= pos.stop_loss:
                if pos.trailing_activated:
                    reason = "Short Trailing Stop Hit"
                elif pos.breakeven_activated:
                    reason = "Short Breakeven Stop Hit"
                else:
                    reason = "Short Stop Loss Hit"
                logger.warning(f"🚨 {reason} for {pair} short at {current_price:.4f} (Stop: {pos.stop_loss:.4f})")
                return ExitSignal(
                    pair=pair,
                    action=SignalAction.SHORT_CLOSE,
                    quantity=pos.quantity,
                    collateral=pos.collateral,
                    order_type="MARKET",
                    reason=reason,
                )

            # Check Take Profit target trigger for short
            if current_price <= pos.take_profit:
                logger.info(f"🎯 Take Profit target reached for {pair} short at {current_price:.4f}")
                return ExitSignal(
                    pair=pair,
                    action=SignalAction.SHORT_CLOSE,
                    quantity=pos.quantity,
                    collateral=pos.collateral,
                    order_type="MARKET",
                    reason="Short Take Profit Target Hit",
                )

        return None

    def evaluate_exits(
        self, pair: str, current_price: float, current_bar: Optional[int] = None
    ) -> Optional[ExitSignal]:
        """Backward-compatible alias for update_price."""
        return self.update_price(pair=pair, current_price=current_price, current_bar=current_bar)

    def get_open_positions_count(self) -> int:
        """Return number of currently active tracked positions."""
        return len(self.positions)

    def get_active_pairs(self) -> List[str]:
        """Return list of pair symbols currently held."""
        return list(self.positions.keys())

    def get_portfolio_risk_heat(self, portfolio_equity: float) -> float:
        """
        Calculate active cumulative downside risk heat:
            H_open = \\sum_{j \\in P} (Q_j * max(0, P_entry - P_stop)) / E_t (for longs)
            H_open += \\sum_{j \\in P} (Q_j * max(0, P_stop - P_entry)) / E_t (for shorts)
        Positions ratcheted to breakeven or trailing profit consume 0 risk heat.
        Returns a float representing portfolio risk heat fraction (e.g. 0.0075 for 0.75%).
        """
        if portfolio_equity <= 0.0:
            return 0.0

        total_downside_risk_usd = 0.0
        for pos in self.positions.values():
            if pos.side == SignalAction.BUY:
                downside_per_unit = max(0.0, pos.entry_price - pos.stop_loss)
                total_downside_risk_usd += pos.quantity * downside_per_unit
            elif pos.side == SignalAction.SHORT_OPEN:
                downside_per_unit = max(0.0, pos.stop_loss - pos.entry_price)
                total_downside_risk_usd += pos.quantity * downside_per_unit

        return total_downside_risk_usd / portfolio_equity
