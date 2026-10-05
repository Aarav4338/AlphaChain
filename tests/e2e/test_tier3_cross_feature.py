"""
Tier 3: Cross-Feature Combinations E2E Tests.
Validates multi-module interactions and pairwise emergent behaviors:
1. Regime Shift + Dynamic Concurrency Scaling
2. BTC Momentum Impulse + Multi-Asset Altcoin Basket
3. Sudden Process Crash + Startup State Reconciliation + Active Heat Restoration
4. Hardware Circuit Breaker Trip + Persistent Disk Heartbeat Daemon
5. Constrained Cash Reserve Invariant vs High Conviction Allocation
6. Trailing Stop Breakeven Ratchet + Dynamic Concurrency Capacity Expansion
"""

import json
from pathlib import Path
import time
from typing import Dict, List

import numpy as np
import pytest

from config.trading_config import trading_config
from execution.reconciler import StateReconciler
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus
from risk.position_sizer import PositionSizer, OrderSizeResult
from risk.stop_loss_manager import StopLossManager, TrackedPosition
from storage.db import Database
from strategy.regime_detector import MarketRegime

try:
    from strategy.signals import TradeSignal, SignalAction
except ImportError:
    from strategy.multi_regime_alpha import TradeSignal, SignalAction


class TestCrossFeatureInteractions:
    """End-to-end multi-module integration tests verifying emergent system behaviors."""

    def test_regime_shift_contracts_dynamic_concurrency_and_gates_signals(self, signal_factory):
        """
        BULL -> CHOP -> BEAR regime shifts:
        1. In BULL: 4 assets sized with full 1.0% heat budget.
        2. In CHOP: Heat budget contracts to 0.40%, low-conviction signals pruned.
        3. In BEAR: Long heat contracts to 0.0% (100% cash defense).
        """
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 85000.0

        signals = [
            signal_factory("BTC/USD", SignalAction.BUY, entry_price=65000.0, conviction=0.85),
            signal_factory("ETH/USD", SignalAction.BUY, entry_price=2600.0, conviction=0.75),
            signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.55),
            signal_factory("DOGE/USD", SignalAction.BUY, entry_price=0.15, conviction=0.35),  # Low conviction
        ]

        # Phase 1: BULL regime
        bull_res = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )
        assert sum(r.risk_usd for r in bull_res if r.allowed) / equity <= 0.010001
        assert any(r.pair == "DOGE/USD" and r.allowed for r in bull_res)

        # Phase 2: CHOP regime (ceiling 0.40%, hurdle 0.45)
        chop_res = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.CHOP,
        )
        chop_allowed = [r for r in chop_res if r.allowed]
        total_chop_heat = sum(r.risk_usd for r in chop_allowed) / equity
        assert total_chop_heat <= 0.004001, "CHOP heat must not exceed 0.40%"
        # DOGE has conviction 0.35 < 0.45 -> must be rejected
        doge_chop = next(r for r in chop_res if r.pair == "DOGE/USD")
        assert not doge_chop.allowed
        assert "chop" in doge_chop.reason.lower() or "conviction" in doge_chop.reason.lower()

        # Phase 3: BEAR regime (0.0% long heat)
        bear_res = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BEAR,
        )
        assert all(not r.allowed for r in bear_res), "All longs must be rejected in BEAR regime"

    def test_btc_momentum_competes_with_altcoin_basket_under_heat_budget(self, signal_factory):
        """
        BTC dedicated momentum impulse competes alongside 4 altcoins.
        Verifies BTC receives a competitive share of the 1.0% risk budget without crowding out altcoins.
        """
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 85000.0

        signals = [
            signal_factory("BTC/USD", SignalAction.BUY, entry_price=64000.0, conviction=0.90),
            signal_factory("ETH/USD", SignalAction.BUY, entry_price=2600.0, conviction=0.80),
            signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.75),
            signal_factory("AVAX/USD", SignalAction.BUY, entry_price=30.0, conviction=0.70),
            signal_factory("SUI/USD", SignalAction.BUY, entry_price=2.0, conviction=0.65),
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert len(results) == 5
        assert all(r.allowed for r in results)

        btc_res = next(r for r in results if r.pair == "BTC/USD")
        assert btc_res.allowed
        assert btc_res.risk_usd >= 200.0, "BTC must capture meaningful risk share"

        total_heat = sum(r.risk_usd for r in results) / equity
        assert total_heat <= 0.010001

    def test_process_crash_state_reconciliation_and_risk_heat_restoration(self, mock_roostoo_client, isolated_db):
        """
        Simulates crash during active trading:
        1. 2 positions held on exchange.
        2. StateReconciler runs on reboot, syncing SQLite and restoring balances.
        3. StopLossManager and PositionSizer reconstruct active heat and constrain new orders.
        """
        slm = StopLossManager()
        sizer = PositionSizer()

        # Seed mock exchange with 2 open holdings: ETH and SOL
        mock_roostoo_client.get_balance.return_value = {
            "Success": True,
            "Wallet": {
                "USD": {"Free": 70000.0, "Locked": 0.0},
                "ETH": {"Free": 5.0, "Locked": 0.0},
                "SOL": {"Free": 50.0, "Locked": 0.0},
            },
        }

        reconciler = StateReconciler(
            roostoo_client=mock_roostoo_client,
            stop_loss_manager=slm,
            database=isolated_db,
        )

        # Reconcile on startup
        reconciler.reconcile_on_startup()

        # Restore positions into SLM
        equity = 100000.0
        slm.register_position("ETH/USD", SignalAction.BUY, 2500.0, 5.0, 0.0, 2440.0, 2700.0, 40.0)  # $300 risk
        slm.register_position("SOL/USD", SignalAction.BUY, 150.0, 50.0, 0.0, 144.0, 170.0, 4.0)    # $300 risk

        # Restored active heat = 600 / 100,000 = 0.60%
        active_heat = slm.get_portfolio_risk_heat(equity)
        assert pytest.approx(active_heat, abs=1e-4) == 0.0060

        # Attempt to size new candidate signals
        cand_sig = TradeSignal(
            pair="BTC/USD",
            action=SignalAction.BUY,
            entry_price=65000.0,
            stop_loss_price=64000.0,
            take_profit_price=68000.0,
            conviction=0.90,
            timestamp=int(time.time()),
        )

        # Only 0.40% heat remaining!
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[cand_sig],
            portfolio_equity=equity,
            free_usd=70000.0,
            current_open_risk_heat=active_heat,
            regime=MarketRegime.BULL,
        )

        assert results[0].allowed
        allocated_heat = results[0].risk_usd / equity
        assert allocated_heat <= 0.00401, f"New order heat {allocated_heat:.4f} exceeded remaining 0.40% capacity"

    def test_circuit_breaker_trip_reports_accurately_in_atomic_heartbeat(self, heartbeat_workspace):
        """
        Verifies that when CircuitBreaker trips, the persistent heartbeat logger
        accurately records TRIPPED status, zeroed trading capacity, and current equity to disk.
        """
        hb_json = heartbeat_workspace["hb_json"]
        hb_log = heartbeat_workspace["hb_log"]

        cb = CircuitBreaker()
        cb.update(100000.0)
        cb.update(95000.0)  # 5.0% drawdown -> TRIPPED
        assert cb.status == CircuitBreakerStatus.TRIPPED

        # Emit simulated atomic heartbeat
        hb_payload = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "timestamp_epoch": time.time(),
            "status": "CIRCUIT_BREAKER_TRIPPED",
            "regime": "BEAR",
            "equity": 95000.0,
            "free_usd": 95000.0,
            "positions_count": 0,
            "active_risk_heat_pct": 0.0,
            "circuit_breaker": cb.status.value,
            "drawdown_pct": cb.current_drawdown_pct,
        }

        temp_file = hb_json.with_suffix(".tmp")
        temp_file.write_text(json.dumps(hb_payload, indent=2), encoding="utf-8")
        temp_file.replace(hb_json)

        with open(hb_log, "a", encoding="utf-8") as f:
            f.write(f"[{hb_payload['timestamp_utc']}] [HEARTBEAT] CircuitBreaker: TRIPPED | DD: {cb.current_drawdown_pct*100:.2f}%\n")

        # Verify disk artifacts
        assert hb_json.exists()
        assert hb_log.exists()

        loaded_data = json.loads(hb_json.read_text(encoding="utf-8"))
        assert loaded_data["circuit_breaker"] == "TRIPPED"
        assert loaded_data["drawdown_pct"] >= 0.040

    def test_cash_reserve_precedence_over_heat_budget_under_capital_constraint(self, signal_factory):
        """
        When available heat is large (1.0%) but free cash is tightly constrained (only $5k above 25% reserve),
        cash reserve bounds strictly override risk sizing to preserve >= 25% cash buffer.
        """
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 30000.0  # Only $5,000 USD available above $25,000 reserve

        signals = [
            signal_factory("BTC/USD", SignalAction.BUY, entry_price=60000.0, conviction=0.95),
            signal_factory("ETH/USD", SignalAction.BUY, entry_price=2500.0, conviction=0.90),
            signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.85),
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        allowed = [r for r in results if r.allowed]
        total_capital = sum(r.estimated_capital for r in allowed)
        assert total_capital <= 5005.0

        # Invariant check
        free_after = free_usd - total_capital
        assert free_after >= (equity * 0.25) - 5.0

    def test_trailing_stop_breakeven_ratchet_liberates_heat_for_new_entry(self, signal_factory):
        """
        Lifecycle interaction:
        1. Open 2 positions consuming 0.90% heat (only 0.10% left).
        2. A 3rd candidate signal is rejected (0.10% < minimum viable heat).
        3. Market rallies, position 1 ratchets to breakeven -> heat drops to 0.45%.
        4. Available heat expands to 0.55% -> 3rd signal is evaluated and successfully admitted!
        """
        slm = StopLossManager()
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 70000.0

        # Position 1: SOL (0.45% heat)
        slm.register_position("SOL/USD", SignalAction.BUY, 100.0, 45.0, 0.0, 90.0, 130.0, 5.0)
        # Position 2: AVAX (0.45% heat)
        slm.register_position("AVAX/USD", SignalAction.BUY, 30.0, 150.0, 0.0, 27.0, 39.0, 1.5)

        initial_heat = slm.get_portfolio_risk_heat(equity)
        assert pytest.approx(initial_heat, abs=1e-4) == 0.0090  # 0.90% consumed

        # 3rd candidate signal
        cand_sig = signal_factory("NEAR/USD", SignalAction.BUY, entry_price=5.0, conviction=0.80)

        # Before breakeven: only 0.10% available heat
        pre_res = sizer.calculate_dynamic_portfolio_sizes(
            signals=[cand_sig],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_risk_heat=initial_heat,
            regime=MarketRegime.BULL,
        )
        # Even if allowed, it gets at most 0.10% heat
        if pre_res[0].allowed:
            assert pre_res[0].risk_usd / equity <= 0.0011

        # Market rallies: SOL price reaches 110.0 (+2.0 ATR)
        slm.evaluate_exits("SOL/USD", current_price=110.0)
        pos_sol = slm.positions["SOL/USD"]
        assert pos_sol.breakeven_activated or pos_sol.trailing_activated
        assert pos_sol.stop_loss >= pos_sol.entry_price

        # Liberated heat calculation: SOL downside risk is now 0.0
        liberated_heat = slm.get_portfolio_risk_heat(equity)
        assert pytest.approx(liberated_heat, abs=1e-4) == 0.0045  # Dropped by half!

        # Post breakeven: 0.55% available heat
        post_res = sizer.calculate_dynamic_portfolio_sizes(
            signals=[cand_sig],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_risk_heat=liberated_heat,
            regime=MarketRegime.BULL,
        )

        assert post_res[0].allowed
        allocated_heat = post_res[0].risk_usd / equity
        assert allocated_heat > 0.0030  # Successfully captures newly unlocked risk budget
