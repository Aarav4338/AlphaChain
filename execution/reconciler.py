"""
State Reconciler & Crash Recovery Engine.
Queries Roostoo's live exchange balances and short positions on startup.
Synchronizes in-memory StopLossManager with actual exchange state,
recovering gracefully from unhandled restarts, EC2 reboots, or network disconnects.
"""
import logging
from typing import Dict, Any, Tuple, Optional

from client.roostoo_client import RoostooClient
from data.candle_manager import CandleManager
from risk.stop_loss_manager import StopLossManager
from storage.db import Database
from strategy.signals import SignalAction

logger = logging.getLogger("CarrotQuant.Reconciler")


class StateReconciler:
    """Synchronizes local state with Roostoo live exchange balances on startup."""

    def __init__(
        self,
        roostoo_client: RoostooClient,
        candle_manager: Optional[CandleManager] = None,
        stop_loss_manager: Optional[StopLossManager] = None,
        database: Optional[Database] = None,
    ):
        self.client: RoostooClient = roostoo_client
        self.candle_manager: Optional[CandleManager] = candle_manager
        self.stop_loss_manager: StopLossManager = stop_loss_manager or StopLossManager()
        self.db: Optional[Database] = database

    def reconcile_on_startup(self) -> Tuple[float, float]:
        """
        Query Roostoo API for balances and short positions.
        Returns: (total_portfolio_equity_usd, free_usd).
        """
        logger.info("🔄 Running startup state reconciliation with Roostoo backend...")

        # 1. Fetch live wallet balances from /v3/balance
        balance_res = self.client.get_balance()
        wallet: Dict[str, Dict[str, float]] = {}
        if balance_res and balance_res.get("Success", False):
            wallet = balance_res.get("SpotWallet") or balance_res.get("Wallet", {})
        else:
            logger.warning("Could not fetch balance from Roostoo; defaulting to local state.")

        # Extract USD balances
        usd_info = wallet.get("USD", {"Free": 100000.0, "Lock": 0.0})
        free_usd = float(usd_info.get("Free", 100000.0))
        locked_usd = float(usd_info.get("Lock", 0.0))

        # 2. Fetch live short positions from /v6/short_positions
        shorts_res = self.client.get_short_positions()
        live_shorts = []
        if shorts_res and shorts_res.get("Success", False):
            live_shorts = shorts_res.get("Positions", [])

        # 3. Load locally saved positions from SQLite
        saved_positions = {p["pair"]: p for p in self.db.get_active_positions()} if self.db is not None else {}

        # 4. Fetch matched orders from Roostoo to extract exact historical entry timestamps
        orders_res = self.client.query_order()
        matched_orders = orders_res.get("OrderMatched", []) if orders_res and orders_res.get("Success", False) else []
        order_fill_times: Dict[str, float] = {}
        for o in matched_orders:
            p = o.get("Pair")
            if p and o.get("Status") == "FILLED":
                cts = o.get("CreateTimestamp")
                if cts and p not in order_fill_times:
                    order_fill_times[p] = float(cts) / 1000.0

        # ----------------------------------------------------------------------
        # Reconcile Spot Long Holdings
        # ----------------------------------------------------------------------
        total_spot_equity = 0.0

        for coin, coin_info in wallet.items():
            if coin == "USD":
                continue

            coin_free = float(coin_info.get("Free", 0.0))
            coin_lock = float(coin_info.get("Lock", 0.0))
            coin_qty = coin_free + coin_lock

            pair = f"{coin}/USD"
            last_price = (self.candle_manager.get_latest_price(pair) or 0.0) if self.candle_manager is not None else 0.0
            coin_value = coin_qty * last_price

            # If coin holding value > $15 (ignore tiny rounding dust like 0.0001 BTC)
            if coin_value > 15.0:
                total_spot_equity += coin_value

                # Reconstruct position in StopLossManager if missing
                if pair not in self.stop_loss_manager.positions:
                    saved = saved_positions.get(pair)
                    entry_p = float(saved["entry_price"]) if saved else last_price
                    stop_p = float(saved["stop_loss"]) if saved else (last_price * 0.95)
                    take_p = float(saved["take_profit"]) if saved else (last_price * 1.10)
                    atr = float(saved["atr"]) if saved else (last_price * 0.03)
                    pos_entry_time = order_fill_times.get(pair, 0.0)

                    self.stop_loss_manager.register_position(
                        pair=pair,
                        side=SignalAction.BUY,
                        entry_price=entry_p,
                        quantity=coin_qty,
                        collateral=0.0,
                        stop_loss=stop_p,
                        take_profit=take_p,
                        atr=atr,
                        entry_time=pos_entry_time,
                    )
                    logger.info(f"Restored Long position for {pair}: {coin_qty} coins (Value: ${coin_value:.2f}, EntryTime: {pos_entry_time})")

        # ----------------------------------------------------------------------
        # Reconcile Spot Short Holdings
        # ----------------------------------------------------------------------
        total_short_value = 0.0

        for s in live_shorts:
            pair = s.get("Pair", "")
            collateral = float(s.get("Collateral", 0.0))
            short_qty = float(s.get("ShortQty", 0.0))
            entry_p = float(s.get("EntryPrice", 0.0))
            pos_val = float(s.get("PositionValue", collateral))

            total_short_value += pos_val

            if pair and pair not in self.stop_loss_manager.positions:
                saved = saved_positions.get(pair)
                stop_p = float(saved["stop_loss"]) if saved else (entry_p * 1.05)
                take_p = float(saved["take_profit"]) if saved else (entry_p * 0.90)
                atr = float(saved["atr"]) if saved else (entry_p * 0.03)
                pos_entry_time = order_fill_times.get(pair, 0.0)

                self.stop_loss_manager.register_position(
                    pair=pair,
                    side=SignalAction.SHORT_OPEN,
                    entry_price=entry_p,
                    quantity=short_qty,
                    collateral=collateral,
                    stop_loss=stop_p,
                    take_profit=take_p,
                    atr=atr,
                    entry_time=pos_entry_time,
                )
                logger.info(f"Restored Short position for {pair}: Collateral=${collateral:.2f}")

        # Compute total portfolio equity
        total_equity = free_usd + locked_usd + total_spot_equity + total_short_value
        logger.info(
            f"✅ State Reconciliation Complete: Total Equity=${total_equity:.2f} | "
            f"Free USD=${free_usd:.2f} | Spot Value=${total_spot_equity:.2f} | "
            f"Short Value=${total_short_value:.2f} | Open Positions={self.stop_loss_manager.get_open_positions_count()}"
        )
        return total_equity, free_usd
