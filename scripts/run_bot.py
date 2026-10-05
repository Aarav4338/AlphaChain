import sys
import os
import time
import json
import signal
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config.settings import settings
from config.trading_config import trading_config
from storage.logger import setup_logger
from storage.db import Database
from client.roostoo_client import RoostooClient
from client.binance_fetcher import BinanceFetcher
from data.candle_manager import CandleManager
from strategy.regime_detector import RegimeDetector
from strategy.multi_regime_alpha import MultiRegimeAlphaStrategy
from strategy.signals import SignalAction
from risk.position_sizer import PositionSizer
from risk.stop_loss_manager import StopLossManager
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus
from execution.order_router import OrderRouter
from execution.reconciler import StateReconciler


class TradingDaemon:
    def __init__(self):
        self.logger = setup_logger(
            name="AlphaChain",
            log_dir=settings.LOGS_DIR,
            log_level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        )
        self.logger.info("Initializing AlphaChain Autonomous Trading Daemon")

        self.running = False
        self._setup_signal_handlers()

        self.db = Database()
        self.client = RoostooClient()

        self.candle_manager = CandleManager(max_buffer_size=250)
        self.binance_fetcher = BinanceFetcher()
        self.regime_detector = RegimeDetector()
        self.strategy = MultiRegimeAlphaStrategy(self.regime_detector)

        self.sizer = PositionSizer()
        self.stop_loss_manager = StopLossManager()
        self.circuit_breaker = CircuitBreaker(initial_equity=trading_config.INITIAL_PORTFOLIO_USD)
        self.order_router = OrderRouter(
            roostoo_client=self.client,
            candle_manager=self.candle_manager,
            position_sizer=self.sizer,
            stop_loss_manager=self.stop_loss_manager,
            database=self.db,
        )
        self.reconciler = StateReconciler(
            roostoo_client=self.client,
            candle_manager=self.candle_manager,
            stop_loss_manager=self.stop_loss_manager,
            database=self.db,
        )

        self.portfolio_equity = trading_config.INITIAL_PORTFOLIO_USD
        self.free_usd = trading_config.INITIAL_PORTFOLIO_USD
        self.locked_usd = 0.0
        self.last_snapshot_time = 0.0
        self.last_heartbeat_time = 0.0
        self.iteration_count = 0

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame) -> None:
        self.logger.warning("Shutdown signal received. Preparing graceful exit...")
        self.running = False

    def bootstrap_historical_candles(self) -> None:
        self.logger.info("Bootstrapping historical candles for indicator initialization...")
        universe = trading_config.ASSET_UNIVERSE

        for pair in universe:
            df_1h = self.binance_fetcher.fetch_klines(
                pair, interval=trading_config.TIMEFRAME_TREND, limit=trading_config.WARMUP_CANDLES_COUNT
            )
            if df_1h is not None and not df_1h.empty:
                self.candle_manager.initialize_pair_history(pair, trading_config.TIMEFRAME_TREND, df_1h)

            df_15m = self.binance_fetcher.fetch_klines(
                pair, interval=trading_config.TIMEFRAME_ENTRY, limit=trading_config.WARMUP_CANDLES_COUNT
            )
            if df_15m is not None and not df_15m.empty:
                self.candle_manager.initialize_pair_history(pair, trading_config.TIMEFRAME_ENTRY, df_15m)

        self.logger.info("Historical candles bootstrapped successfully.")

    def run(self) -> None:
        server_time_res = self.client.check_server_time()
        if not server_time_res:
            self.logger.error("Failed to reach Roostoo API server.")
            return

        exchange_info = self.client.get_exchange_info()
        if exchange_info:
            self.sizer.update_precisions_from_exchange_info(exchange_info)

        self.bootstrap_historical_candles()

        self.portfolio_equity, self.free_usd = self.reconciler.reconcile_on_startup()
        self.circuit_breaker.peak_equity = max(self.circuit_breaker.peak_equity, self.portfolio_equity)

        self.logger.info(
            f"Startup ready. Equity: ${self.portfolio_equity:,.2f} | "
            f"Free USD: ${self.free_usd:,.2f} | Positions: {self.stop_loss_manager.get_open_positions_count()}"
        )

        self.running = True

        while self.running:
            try:
                loop_start = time.time()
                self.iteration_count += 1

                self._poll_market_tickers()
                self._update_portfolio_valuation()
                self._evaluate_circuit_breaker()
                self._manage_active_positions()

                if self.circuit_breaker.is_trading_allowed():
                    self._evaluate_and_enter_trades()

                self._record_hourly_snapshot()
                self._emit_heartbeat()

                elapsed = time.time() - loop_start
                sleep_needed = max(1.0, trading_config.MAIN_LOOP_SLEEP_SECONDS - elapsed)
                time.sleep(sleep_needed)

            except Exception as e:
                self.logger.exception(f"Unhandled exception in trading loop: {e}")
                time.sleep(5.0)

        self.logger.info("Trading daemon terminated gracefully.")

    def _poll_market_tickers(self) -> None:
        res = self.client.get_ticker()
        if not res or not res.get("Success", False):
            return

        data: Dict[str, Dict[str, Any]] = res.get("Data", {})
        universe_set = set(trading_config.ASSET_UNIVERSE)
        now_utc = pd.Timestamp.now(tz="UTC")

        for pair, info in data.items():
            if pair in universe_set:
                last_price = float(info.get("LastPrice", 0.0))
                max_bid = float(info.get("MaxBid", last_price))
                min_ask = float(info.get("MinAsk", last_price))

                if last_price > 0:
                    self.candle_manager.update_tick(
                        pair=pair,
                        last_price=last_price,
                        max_bid=max_bid,
                        min_ask=min_ask,
                        timestamp=now_utc,
                    )

    def _update_portfolio_valuation(self) -> None:
        positions_value = 0.0
        for pair, pos in self.stop_loss_manager.positions.items():
            last_p = self.candle_manager.get_latest_price(pair) or pos.entry_price
            if pos.side == SignalAction.BUY:
                positions_value += pos.quantity * last_p
            elif pos.side == SignalAction.SHORT_OPEN:
                pnl = (pos.entry_price - last_p) * pos.quantity
                positions_value += max(0.0, pos.collateral + pnl)

        self.portfolio_equity = self.free_usd + self.locked_usd + positions_value

    def _evaluate_circuit_breaker(self) -> None:
        cb_status = self.circuit_breaker.update_equity(self.portfolio_equity)

        if cb_status == CircuitBreakerStatus.TRIPPED:
            self.logger.critical("Circuit breaker threshold breached. Triggering emergency liquidation.")
            self.order_router.emergency_liquidate_all_to_usd()
            self.circuit_breaker.acknowledge_liquidation()

            bal = self.client.get_balance()
            if bal and bal.get("Success", False):
                wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
                self.free_usd = float(wallet.get("USD", {}).get("Free", self.portfolio_equity))
                self.portfolio_equity = self.free_usd

    def _manage_active_positions(self) -> None:
        active_pairs = list(self.stop_loss_manager.positions.keys())
        for pair in active_pairs:
            pos = self.stop_loss_manager.positions.get(pair)
            if not pos:
                continue

            last_p = self.candle_manager.get_latest_price(pair)
            if last_p is None:
                continue

            exit_sig = self.stop_loss_manager.update_price(pair, last_p)
            if exit_sig:
                success = self.order_router.execute_exit(exit_sig, current_price=last_p)
                if success:
                    self.strategy.record_exit(pair, self.iteration_count)
                    bal = self.client.get_balance()
                    if bal and bal.get("Success", False):
                        wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
                        self.free_usd = float(wallet.get("USD", {}).get("Free", self.free_usd))
                        self.locked_usd = float(wallet.get("USD", {}).get("Lock", 0.0))

    def _evaluate_and_enter_trades(self) -> None:
        active_pairs = list(self.stop_loss_manager.positions.keys())
        signals = self.strategy.generate_signals(
            self.candle_manager,
            active_pairs=active_pairs,
            current_bar_index=self.iteration_count,
        )

        if not signals:
            return

        signals.sort(key=lambda s: s.conviction, reverse=True)

        regime_res = self.regime_detector.detect_regime(self.candle_manager)
        current_heat = self.stop_loss_manager.get_portfolio_risk_heat(self.portfolio_equity)

        sized_orders = self.sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=self.portfolio_equity,
            free_usd=self.free_usd,
            current_open_risk_heat=current_heat,
            regime=regime_res.regime,
        )

        for order in sized_orders:
            if not order.allowed:
                continue
            sig = next((s for s in signals if s.pair == order.pair), None)
            if sig is None:
                continue

            self.logger.info(
                f"Signal: {sig.action.value} {sig.pair} | Conviction: {sig.conviction:.2f} | "
                f"Risk: ${order.risk_usd:.2f} ({(order.risk_usd / self.portfolio_equity) * 100:.3f}%) | "
                f"Reason: {sig.reason}"
            )
            success = self.order_router.execute_entry(
                signal=sig,
                portfolio_equity=self.portfolio_equity,
                free_usd=self.free_usd,
                prefer_maker=True,
                size_result=order,
            )
            if success:
                bal = self.client.get_balance()
                if bal and bal.get("Success", False):
                    wallet = bal.get("SpotWallet") or bal.get("Wallet", {})
                    self.free_usd = float(wallet.get("USD", {}).get("Free", self.free_usd))
                    self.locked_usd = float(wallet.get("USD", {}).get("Lock", 0.0))

    def _record_hourly_snapshot(self) -> None:
        now = time.time()
        if now - self.last_snapshot_time >= trading_config.EQUITY_SNAPSHOT_INTERVAL_SECONDS:
            self.last_snapshot_time = now

            init_eq = trading_config.INITIAL_PORTFOLIO_USD
            total_profit_pct = ((self.portfolio_equity / init_eq) - 1.0) * 100.0 if init_eq > 0 else 0.0
            drawdown_pct = self.circuit_breaker.current_drawdown_pct * 100.0
            positions_value = max(0.0, self.portfolio_equity - self.free_usd)

            self.db.record_portfolio_snapshot(
                total_equity=self.portfolio_equity,
                free_usd=self.free_usd,
                locked_usd=0.0,
                positions_value=positions_value,
                unrealized_pnl=positions_value,
                total_profit_pct=total_profit_pct,
                drawdown_pct=drawdown_pct,
            )

    def _emit_heartbeat(self) -> None:
        """Log system status and market regime heartbeat once per minute, writing persistent on-disk state."""
        now = time.time()
        if now - self.last_heartbeat_time >= 60.0:
            self.last_heartbeat_time = now
            regime_res = self.regime_detector.detect_regime(self.candle_manager)
            active_count = self.stop_loss_manager.get_open_positions_count()
            cb_status = (
                self.circuit_breaker.status.value
                if hasattr(self.circuit_breaker.status, "value")
                else str(self.circuit_breaker.status)
            )
            regime_val = (
                regime_res.regime.value
                if hasattr(regime_res.regime, "value")
                else str(regime_res.regime)
            )
            iso_now = datetime.now(timezone.utc).isoformat()
            max_pos = getattr(trading_config, "MAX_CONCURRENT_POSITIONS", "dynamic")

            # 1. Standard application log
            self.logger.info(
                f"[HEARTBEAT] Regime: {regime_val} | "
                f"Equity: ${self.portfolio_equity:,.2f} | Free USD: ${self.free_usd:,.2f} | "
                f"Positions: {active_count}/{max_pos} | "
                f"CircuitBreaker: {cb_status}"
            )

            # Check optional active risk heat if available from stop loss manager
            active_risk_heat = 0.0
            if hasattr(self.stop_loss_manager, "get_portfolio_risk_heat"):
                try:
                    active_risk_heat = round(
                        float(self.stop_loss_manager.get_portfolio_risk_heat(self.portfolio_equity)) * 100.0, 4
                    )
                except Exception:
                    pass

            # 2. Atomic write to logs/heartbeat.json (safe for concurrent external watchdogs)
            heartbeat_payload = {
                "timestamp_utc": iso_now,
                "epoch_seconds": now,
                "status": "HEALTHY",
                "regime": regime_val,
                "equity": round(float(self.portfolio_equity), 2),
                "free_usd": round(float(self.free_usd), 2),
                "locked_usd": round(float(self.locked_usd), 2),
                "positions_count": active_count,
                "active_risk_heat_pct": active_risk_heat,
                "circuit_breaker": cb_status,
                "iteration_count": self.iteration_count,
                "pid": os.getpid(),
            }

            try:
                logs_dir = settings.LOGS_DIR
                logs_dir.mkdir(parents=True, exist_ok=True)

                # Atomic JSON write via temporary file + atomic rename
                temp_json_path = logs_dir / f"heartbeat_{os.getpid()}_{int(now * 1000)}.tmp"
                target_json_path = logs_dir / "heartbeat.json"
                with open(temp_json_path, "w", encoding="utf-8") as f:
                    json.dump(heartbeat_payload, f, indent=2)
                temp_json_path.replace(target_json_path)

                # 3. Persistent append to logs/heartbeat.log
                heartbeat_log_path = logs_dir / "heartbeat.log"
                log_line = (
                    f"{iso_now} | STATUS=HEALTHY | REGIME={regime_val} | "
                    f"EQUITY={self.portfolio_equity:.2f} | FREE_USD={self.free_usd:.2f} | "
                    f"POSITIONS={active_count} | CB={cb_status} | ITER={self.iteration_count}\n"
                )
                with open(heartbeat_log_path, "a", encoding="utf-8") as f:
                    f.write(log_line)

            except Exception as e:
                self.logger.warning(f"Failed to write on-disk heartbeat: {e}")


if __name__ == "__main__":
    daemon = TradingDaemon()
    daemon.run()
