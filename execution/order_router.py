import logging
from typing import Optional, Any

from client.roostoo_client import RoostooClient
from data.candle_manager import CandleManager
from strategy.signals import TradeSignal, SignalAction
from risk.position_sizer import PositionSizer, OrderSizeResult
from risk.stop_loss_manager import StopLossManager, ExitSignal, PyramidCandidate
from storage.db import Database

logger = logging.getLogger("AlphaChain.OrderRouter")


class OrderRouter:
    def __init__(
        self,
        roostoo_client: RoostooClient,
        candle_manager: CandleManager,
        position_sizer: PositionSizer,
        stop_loss_manager: StopLossManager,
        database: Database,
        email_alerter: Optional[object] = None,
    ):
        self.client: RoostooClient = roostoo_client
        self.candle_manager: CandleManager = candle_manager
        self.sizer: PositionSizer = position_sizer
        self.stop_loss_manager: StopLossManager = stop_loss_manager
        self.db: Database = database
        self.email_alerter = email_alerter

    def execute_entry(
        self,
        signal: TradeSignal,
        portfolio_equity: float,
        free_usd: float,
        prefer_maker: bool = True,
        size_result: Optional[OrderSizeResult] = None,
    ) -> bool:
        pair = signal.pair

        if size_result is not None:
            size_res = size_result
        else:
            current_positions = self.stop_loss_manager.get_open_positions_count()
            size_res = self.sizer.calculate_size(
                signal=signal,
                portfolio_equity=portfolio_equity,
                free_usd=free_usd,
                current_open_positions_count=current_positions,
            )

        if not size_res.allowed:
            logger.info(f"Entry declined for {pair}: {size_res.reason}")
            return False

        if signal.action == SignalAction.BUY:
            bid_ask = self.candle_manager.get_latest_bid_ask(pair)
            order_price = None
            order_type = "MARKET"

            if prefer_maker and bid_ask:
                best_bid, _ = bid_ask
                order_price = best_bid
                order_type = "LIMIT"

            logger.info(f"Placing BUY order for {pair}: Qty={size_res.quantity} | Type={order_type} | LimitPrice={order_price}")

            res = self.client.place_order(
                pair=pair,
                side="BUY",
                quantity=size_res.quantity,
                price=order_price,
                order_type=order_type,
            )

            if res and res.get("Success", False):
                order_detail = res.get("OrderDetail", {})
                filled_price = order_detail.get("Price", signal.entry_price)
                order_id = str(order_detail.get("OrderID", ""))
                fee = order_detail.get("CommissionChargeValue", 0.0)

                self.stop_loss_manager.register_position(
                    pair=pair,
                    side=SignalAction.BUY,
                    entry_price=filled_price,
                    quantity=size_res.quantity,
                    collateral=0.0,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    atr=signal.atr,
                )

                self.db.log_trade(
                    pair=pair,
                    side="BUY",
                    order_type=order_type,
                    price=filled_price,
                    quantity=size_res.quantity,
                    fee=fee,
                    order_id=order_id,
                    reason=signal.reason,
                )
                self.db.save_active_position(
                    pair=pair,
                    side="BUY",
                    entry_price=filled_price,
                    quantity=size_res.quantity,
                    collateral=0.0,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    atr=signal.atr,
                    highest_price=filled_price,
                    lowest_price=filled_price,
                )
                logger.info(f"BUY executed for {pair} @ {filled_price:.4f} (Order #{order_id})")
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute BUY for {pair}: {err}")
                return False

        elif signal.action == SignalAction.SHORT_OPEN:
            logger.info(f"Opening Short for {pair}: Collateral=${size_res.collateral:.2f} USD")
            res = self.client.short_open(pair=pair, collateral=size_res.collateral)

            if res and res.get("Success", False):
                filled_price = res.get("EntryPrice", signal.entry_price)
                short_id = str(res.get("ID", ""))
                short_qty = res.get("ShortQty", size_res.quantity)
                fee = res.get("OpenFee", 0.0)

                self.stop_loss_manager.register_position(
                    pair=pair,
                    side=SignalAction.SHORT_OPEN,
                    entry_price=filled_price,
                    quantity=short_qty,
                    collateral=size_res.collateral,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    atr=signal.atr,
                )

                self.db.log_trade(
                    pair=pair,
                    side="SHORT_OPEN",
                    order_type="MARKET",
                    price=filled_price,
                    quantity=short_qty,
                    collateral=size_res.collateral,
                    fee=fee,
                    order_id=short_id,
                    reason=signal.reason,
                )
                self.db.save_active_position(
                    pair=pair,
                    side="SHORT_OPEN",
                    entry_price=filled_price,
                    quantity=short_qty,
                    collateral=size_res.collateral,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    atr=signal.atr,
                    highest_price=filled_price,
                    lowest_price=filled_price,
                )
                logger.info(f"SHORT_OPEN executed for {pair} @ {filled_price:.4f} (Short #{short_id})")
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute SHORT_OPEN for {pair}: {err}")
                return False

        return False

    def execute_pyramid_scale_in(
        self,
        candidate: Any,
        current_price: float,
        size_res: Any,
    ) -> bool:
        """
        Execute scale-in / pyramiding order on an existing winning position.
        Updates in-memory position state and SQLite database.
        """
        pair = candidate.pair
        pos = self.stop_loss_manager.positions.get(pair)
        if not pos:
            return False

        if candidate.side == SignalAction.BUY:
            logger.info(f"Pyramiding BUY into {pair}: Qty={size_res.quantity} @ ~${current_price:.4f}")
            res = self.client.place_order(
                pair=pair,
                side="BUY",
                quantity=size_res.quantity,
                price=None,
                order_type="MARKET",
            )
            if res and res.get("Success", False):
                order_detail = res.get("OrderDetail", {})
                filled_price = order_detail.get("Price", current_price)
                order_id = str(order_detail.get("OrderID", ""))
                fee = order_detail.get("CommissionChargeValue", 0.0)

                self.stop_loss_manager.add_pyramid_tranche(
                    pair=pair,
                    added_qty=size_res.quantity,
                    fill_price=filled_price,
                    new_stop_loss=size_res.stop_loss_price,
                )
                self.db.log_trade(
                    pair=pair,
                    side="BUY_PYRAMID",
                    order_type="MARKET",
                    price=filled_price,
                    quantity=size_res.quantity,
                    fee=fee,
                    order_id=order_id,
                    reason=candidate.reason,
                )
                self.db.save_active_position(
                    pair=pair,
                    side="BUY",
                    entry_price=pos.entry_price,
                    quantity=pos.quantity,
                    collateral=0.0,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    atr=pos.atr,
                    highest_price=pos.highest_price,
                    lowest_price=pos.lowest_price,
                )
                logger.info(f"Pyramid scale-in filled for {pair} @ {filled_price:.4f} (Order #{order_id})")
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute pyramid BUY for {pair}: {err}")
                return False

        elif candidate.side == SignalAction.SHORT_OPEN:
            logger.info(f"Pyramiding SHORT into {pair}: Collateral=${size_res.collateral:.2f} USD")
            res = self.client.short_open(pair=pair, collateral=size_res.collateral)
            if res and res.get("Success", False):
                filled_price = res.get("EntryPrice", current_price)
                short_id = str(res.get("ID", ""))
                short_qty = res.get("ShortQty", size_res.quantity)
                fee = res.get("OpenFee", 0.0)

                self.stop_loss_manager.add_pyramid_tranche(
                    pair=pair,
                    added_qty=short_qty,
                    fill_price=filled_price,
                    added_collateral=size_res.collateral,
                    new_stop_loss=size_res.stop_loss_price,
                )
                self.db.log_trade(
                    pair=pair,
                    side="SHORT_PYRAMID",
                    order_type="MARKET",
                    price=filled_price,
                    quantity=short_qty,
                    collateral=size_res.collateral,
                    fee=fee,
                    order_id=short_id,
                    reason=candidate.reason,
                )
                self.db.save_active_position(
                    pair=pair,
                    side="SHORT_OPEN",
                    entry_price=pos.entry_price,
                    quantity=pos.quantity,
                    collateral=pos.collateral,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    atr=pos.atr,
                    highest_price=pos.highest_price,
                    lowest_price=pos.lowest_price,
                )
                logger.info(f"Pyramid SHORT filled for {pair} @ {filled_price:.4f} (Short #{short_id})")
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute pyramid SHORT for {pair}: {err}")
                return False

        return False

    def execute_exit(self, exit_signal: ExitSignal, current_price: float) -> bool:
        pair = exit_signal.pair
        pos = self.stop_loss_manager.positions.get(pair)

        if exit_signal.action == SignalAction.SELL:
            order_type = exit_signal.order_type.upper()
            price = current_price if order_type == "LIMIT" else None

            logger.info(f"Executing SELL for {pair}: Qty={exit_signal.quantity} | Type={order_type}")
            res = self.client.place_order(
                pair=pair,
                side="SELL",
                quantity=exit_signal.quantity,
                price=price,
                order_type=order_type,
            )

            if res and res.get("Success", False):
                order_detail = res.get("OrderDetail", {})
                filled_price = order_detail.get("Price", current_price)
                fee = order_detail.get("CommissionChargeValue", 0.0)
                order_id = str(order_detail.get("OrderID", ""))

                entry_price = pos.entry_price if pos else filled_price
                realized_pnl = (filled_price - entry_price) * exit_signal.quantity
                pnl_pct = ((filled_price / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0

                self.stop_loss_manager.remove_position(pair)
                self.db.remove_active_position(pair)
                self.db.log_trade(
                    pair=pair,
                    side="SELL",
                    order_type=order_type,
                    price=filled_price,
                    quantity=exit_signal.quantity,
                    fee=fee,
                    realized_pnl=realized_pnl,
                    order_id=order_id,
                    reason=exit_signal.reason,
                )

                logger.info(
                    f"SELL completed for {pair} @ {filled_price:.4f} | "
                    f"PnL: ${realized_pnl:.2f} ({pnl_pct:.2f}%) | Reason: {exit_signal.reason}"
                )
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute SELL for {pair}: {err}")
                return False

        elif exit_signal.action == SignalAction.SHORT_CLOSE:
            logger.info(f"Executing SHORT_CLOSE for {pair}: Qty={exit_signal.quantity}")
            res = self.client.short_close(pair=pair, close_qty=exit_signal.quantity)

            if res and res.get("Success", False):
                close_price = res.get("ClosePrice", current_price)
                realized_pnl = res.get("RealizedPNL", 0.0)
                close_fee = res.get("CloseFee", 0.0)

                entry_price = pos.entry_price if pos else close_price
                pnl_pct = ((entry_price / close_price) - 1.0) * 100.0 if close_price > 0 else 0.0

                self.stop_loss_manager.remove_position(pair)
                self.db.remove_active_position(pair)
                self.db.log_trade(
                    pair=pair,
                    side="SHORT_CLOSE",
                    order_type="MARKET",
                    price=close_price,
                    quantity=exit_signal.quantity,
                    collateral=exit_signal.collateral,
                    fee=close_fee,
                    realized_pnl=realized_pnl,
                    reason=exit_signal.reason,
                )

                logger.info(
                    f"SHORT_CLOSE completed for {pair} @ {close_price:.4f} | "
                    f"PnL: ${realized_pnl:.2f} ({pnl_pct:.2f}%) | Reason: {exit_signal.reason}"
                )
                return True
            else:
                err = res.get("ErrMsg") if res else "No response"
                logger.error(f"Failed to execute SHORT_CLOSE for {pair}: {err}")
                return False

        return False

    def emergency_liquidate_all_to_usd(self) -> None:
        logger.critical("EMERGENCY LIQUIDATION: Liquidating all positions to USD cash")
        active_pairs = list(self.stop_loss_manager.positions.keys())

        for pair in active_pairs:
            pos = self.stop_loss_manager.positions.get(pair)
            if not pos:
                continue

            last_price = self.candle_manager.get_latest_price(pair) or pos.entry_price

            if pos.side == SignalAction.BUY:
                exit_sig = ExitSignal(
                    pair=pair,
                    action=SignalAction.SELL,
                    quantity=pos.quantity,
                    collateral=0.0,
                    order_type="MARKET",
                    reason="EMERGENCY_CIRCUIT_BREAKER_LIQUIDATION",
                )
                self.execute_exit(exit_sig, current_price=last_price)

            elif pos.side == SignalAction.SHORT_OPEN:
                exit_sig = ExitSignal(
                    pair=pair,
                    action=SignalAction.SHORT_CLOSE,
                    quantity=pos.quantity,
                    collateral=pos.collateral,
                    order_type="MARKET",
                    reason="EMERGENCY_CIRCUIT_BREAKER_LIQUIDATION",
                )
                self.execute_exit(exit_sig, current_price=last_price)
