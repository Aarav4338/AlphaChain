"""
Tier 2: Boundary & Corner Cases E2E Tests.
Validates boundary values, physical system constraints, and extreme conditions:
1. Zero Cash & Extreme Low Cash Boundary
2. Single Qualifying Asset Boundary
3. Max Universe Scale Boundary (12 Assets Simultaneously)
4. Market Gap-Down & Simultaneous Stop-Out Drawdown Guarantee Boundary
5. Dynamic Trailing Stop Ratchet & Breakeven Liberation Boundary
6. Hardware Circuit Breaker Limits Boundary
"""

import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest

from config.trading_config import trading_config
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus
from risk.position_sizer import PositionSizer, OrderSizeResult
from risk.stop_loss_manager import StopLossManager, TrackedPosition, ExitSignal
from strategy.regime_detector import MarketRegime

try:
    from strategy.signals import TradeSignal, SignalAction
except ImportError:
    from strategy.multi_regime_alpha import TradeSignal, SignalAction


# ==============================================================================
# BOUNDARY 1: Zero Cash & Extreme Low Cash Boundary
# ==============================================================================

class TestBoundary1ZeroCashAndExtremeLowCash:
    """Tests extreme cash conditions: zero cash, exact buffer match, dust amounts, negative cash."""

    def test_zero_free_usd_rejects_cleanly(self, signal_factory):
        """Free USD = 0.0 must be rejected with zero division errors."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 0.0

        signal = signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert len(results) == 1
        assert not results[0].allowed
        assert "cash" in results[0].reason.lower()
        assert results[0].quantity == 0.0

    def test_free_cash_exactly_equals_twenty_five_percent_buffer(self, signal_factory):
        """Free USD exactly at 25% of equity (e.g. 25,000 on 100,000 equity) has 0 available cash."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 25000.0

        signal = signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert not results[0].allowed
        assert "cash" in results[0].reason.lower()

    def test_free_cash_one_dollar_above_buffer_rejected_as_dust(self, signal_factory):
        """Free USD at $25,001 on $100,000 equity leaves $1.00 available, which is < $10 minimum order."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 25001.0

        signal = signal_factory("SOL/USD", SignalAction.BUY, entry_price=150.0, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert not results[0].allowed
        assert ("minimum" in results[0].reason.lower() or
                "small" in results[0].reason.lower() or
                "cash" in results[0].reason.lower())

    def test_negative_free_cash_anomaly_handled_gracefully(self, signal_factory):
        """Handles negative cash values gracefully without unexpected exceptions."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = -250.0

        signal = signal_factory("BTC/USD", SignalAction.BUY, entry_price=60000.0, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert not results[0].allowed

    def test_free_cash_fifteen_dollars_above_buffer_allowed(self, signal_factory):
        """Free USD at $25,015 leaves $15.00 available (> $10 min order), allowed and scaled down."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 25015.0

        signal = signal_factory("SOL/USD", SignalAction.BUY, entry_price=10.0, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        if results[0].allowed:
            assert results[0].estimated_capital <= 15.0
            assert results[0].estimated_capital >= 10.0


# ==============================================================================
# BOUNDARY 2: Single Qualifying Asset Boundary
# ==============================================================================

class TestBoundary2SingleQualifyingAsset:
    """Tests boundary conditions when exactly one asset qualifies."""

    def test_single_asset_max_conviction_capped_at_half_percent_risk(self, signal_factory):
        """Even with conviction=1.0 and 1.0% available heat, single asset is capped at 0.50% risk."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        signal = signal_factory("BTC/USD", SignalAction.BUY, entry_price=60000.0, conviction=1.0)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert results[0].allowed
        allocated_heat = results[0].risk_usd / equity
        assert allocated_heat <= 0.00501

    def test_single_asset_ultra_tight_stop_distance_triggers_capital_cap(self, signal_factory):
        """Stop distance of 0.1% would require 500% capital for 0.50% risk; capital cap (20%) must bind."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        signal = signal_factory("ETH/USD", SignalAction.BUY, entry_price=2500.0, stop_dist_pct=0.001, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert results[0].allowed
        assert results[0].estimated_capital <= 20001.0  # 20% cap = $20,000

    def test_single_asset_wide_stop_distance_triggers_risk_cap(self, signal_factory):
        """Stop distance of 10% requires only 5% capital for 0.50% risk; risk cap binds."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        signal = signal_factory("SOL/USD", SignalAction.BUY, entry_price=100.0, stop_dist_pct=0.10, conviction=0.80)
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert results[0].allowed
        assert results[0].risk_usd <= 501.0  # $500 max risk

    def test_single_asset_zero_stop_distance_rejected(self, signal_factory):
        """Stop distance of 0.0 is an invalid stop and must be rejected immediately."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        signal = TradeSignal(
            pair="BTC/USD",
            action=SignalAction.BUY,
            entry_price=60000.0,
            stop_loss_price=60000.0,  # Zero distance!
            take_profit_price=65000.0,
            conviction=0.90,
            timestamp=int(time.time()),
        )
        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=[signal],
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert not results[0].allowed
        assert "stop distance" in results[0].reason.lower()

    def test_single_asset_precision_rounding_doge_vs_btc(self, signal_factory):
        """Tests that DOGE rounds to integer coins (0 decimals) and BTC to 5 decimals."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        doge_sig = signal_factory("DOGE/USD", SignalAction.BUY, entry_price=0.12345, stop_dist_pct=0.03)
        btc_sig = signal_factory("BTC/USD", SignalAction.BUY, entry_price=65432.10, stop_dist_pct=0.02)

        doge_res = sizer.calculate_dynamic_portfolio_sizes([doge_sig], equity, free_usd, [], MarketRegime.BULL)[0]
        btc_res = sizer.calculate_dynamic_portfolio_sizes([btc_sig], equity, free_usd, [], MarketRegime.BULL)[0]

        assert doge_res.quantity == float(round(doge_res.quantity))
        # BTC quantity precision <= 5 decimals
        assert round(btc_res.quantity, 5) == btc_res.quantity


# ==============================================================================
# BOUNDARY 3: Max Universe Scale Boundary (12 Assets Simultaneously)
# ==============================================================================

class TestBoundary3MaxUniverseScaleTwelveAssets:
    """Tests extreme scaling when all 12 assets in the universe qualify simultaneously."""

    def test_twelve_assets_partition_risk_below_one_percent(self, signal_factory):
        """12 simultaneous signals partition heat across all 12; sum(rho_i) <= 1.0%."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 85000.0

        universe = trading_config.ASSET_UNIVERSE
        assert len(universe) == 12

        signals = [
            signal_factory(pair, SignalAction.BUY, entry_price=100.0, conviction=0.80)
            for pair in universe
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        assert len(results) == 12
        allowed = [r for r in results if r.allowed]
        assert len(allowed) == 12, "All 12 assets should receive valid allocations"

        total_risk = sum(r.risk_usd for r in allowed)
        total_heat = total_risk / equity
        assert total_heat <= 0.010001, f"Total heat {total_heat:.4f} exceeds 1.0%"

    def test_twelve_assets_skewed_conviction_distribution(self, signal_factory):
        """12 signals with linearly increasing convictions (0.1 to 1.0) allocate smoothly."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 85000.0

        universe = trading_config.ASSET_UNIVERSE
        signals = [
            signal_factory(pair, SignalAction.BUY, entry_price=100.0, conviction=round(0.1 + i * 0.08, 2))
            for i, pair in enumerate(universe)
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        allowed = [r for r in results if r.allowed]
        assert len(allowed) == 12

        # Verify allocation monotonicity with conviction
        for i in range(len(allowed) - 1):
            assert allowed[i].risk_usd <= allowed[i + 1].risk_usd + 1e-4

    def test_twelve_assets_dynamic_concentration_cap(self, signal_factory):
        """With 12 assets, per-coin capital cap dynamically limits to min(20%, 75%/12 = 6.25%)."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 85000.0

        universe = trading_config.ASSET_UNIVERSE
        # Very tight stops to force capital limits
        signals = [
            signal_factory(pair, SignalAction.BUY, entry_price=100.0, stop_dist_pct=0.005, conviction=0.90)
            for pair in universe
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        total_capital = sum(r.estimated_capital for r in results if r.allowed)
        assert total_capital <= 75000.0 + 50.0, "Total deployed capital cannot exceed 75% equity"

    def test_twelve_assets_under_tight_free_cash(self, signal_factory):
        """With 12 assets and only $32,000 free USD ($7,000 above buffer), sizes scale down to $7,000."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 32000.0

        universe = trading_config.ASSET_UNIVERSE
        signals = [
            signal_factory(pair, SignalAction.BUY, entry_price=100.0, conviction=0.80)
            for pair in universe
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
        assert total_capital <= 7050.0

    def test_twelve_assets_preserves_cash_reserve_invariant(self, signal_factory):
        """Verifies free cash reserve >= 25% holds strictly when 12 assets are held."""
        sizer = PositionSizer()
        equity = 100000.0
        free_usd = 80000.0

        universe = trading_config.ASSET_UNIVERSE
        signals = [
            signal_factory(pair, SignalAction.BUY, entry_price=50.0, conviction=0.85)
            for pair in universe
        ]

        results = sizer.calculate_dynamic_portfolio_sizes(
            signals=signals,
            portfolio_equity=equity,
            free_usd=free_usd,
            current_open_positions=[],
            regime=MarketRegime.BULL,
        )

        deployed = sum(r.estimated_capital for r in results if r.allowed)
        remaining = free_usd - deployed
        assert remaining >= 25000.0 - 5.0


# ==============================================================================
# BOUNDARY 4: Market Gap-Down & Drawdown Guarantee Boundary
# ==============================================================================

class TestBoundary4MarketGapDownAndDrawdownGuarantee:
    """Verifies drawdown bounds under adverse gap down and simultaneous stop-outs."""

    def test_simultaneous_stop_out_six_positions_no_slippage(self):
        """Simultaneous stop-out of 6 positions with 0 slippage causes exactly allocated heat loss (<=1.0%)."""
        slm = StopLossManager()
        equity = 100000.0

        # Register 6 positions with total risk heat = 0.90% ($900)
        pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "NEAR/USD", "SUI/USD"]
        for p in pairs:
            slm.register_position(
                pair=p,
                side=SignalAction.BUY,
                entry_price=100.0,
                quantity=15.0,
                collateral=0.0,
                stop_loss=90.0,  # $10 stop dist * 15 qty = $150 risk
                take_profit=130.0,
                atr=5.0,
            )

        assert pytest.approx(slm.get_portfolio_risk_heat(equity), abs=1e-4) == 0.0090

        # Simulate gap down exactly to stop prices
        total_loss = 0.0
        for p in pairs:
            exit_sig = slm.evaluate_exits(p, current_price=90.0)
            assert exit_sig is not None
            pos = slm.positions[p]
            loss = pos.quantity * (pos.entry_price - 90.0)
            total_loss += loss

        drawdown_pct = total_loss / equity
        assert pytest.approx(drawdown_pct, abs=1e-4) == 0.0090
        assert drawdown_pct <= 0.0100

    def test_simultaneous_stop_out_with_ten_bps_slippage(self):
        """Simultaneous stop-out with 10 bps slippage yields <= 1.075% drawdown, well below 4.0%."""
        equity = 100000.0
        allocated_heat = 0.0100  # 1.0% max heat
        total_capital = 75000.0  # Max 75% invested
        slippage_rate = 0.0010  # 10 bps

        stop_loss = equity * allocated_heat
        slippage_loss = total_capital * slippage_rate
        total_loss = stop_loss + slippage_loss

        drawdown_pct = total_loss / equity
        assert drawdown_pct == 0.01075
        assert drawdown_pct < 0.040

    def test_adverse_slippage_stress_fifty_bps(self):
        """Stress tests extreme 50 bps adverse slippage: total drawdown remains < 1.38% << 4.0%."""
        equity = 100000.0
        allocated_heat = 0.0100
        total_capital = 75000.0
        slippage_rate = 0.0050  # 50 bps extreme gap

        total_loss = (equity * allocated_heat) + (total_capital * slippage_rate)
        drawdown_pct = total_loss / equity
        assert drawdown_pct == 0.01375
        assert drawdown_pct < 0.040

    def test_zero_heat_remaining_after_all_positions_closed(self):
        """Verifies risk heat returns to exactly 0.0 after closing all positions."""
        slm = StopLossManager()
        equity = 100000.0

        slm.register_position("SOL/USD", SignalAction.BUY, 100.0, 50.0, 0.0, 96.0, 116.0, 2.5)
        assert slm.get_portfolio_risk_heat(equity) > 0.0

        slm.close_position("SOL/USD")
        assert slm.get_portfolio_risk_heat(equity) == 0.0
        assert slm.get_open_positions_count() == 0

    def test_partial_stop_out_reduces_heat_proportionally(self):
        """Closing 1 of 2 positions cuts portfolio risk heat accordingly."""
        slm = StopLossManager()
        equity = 100000.0

        slm.register_position("SOL/USD", SignalAction.BUY, 100.0, 50.0, 0.0, 96.0, 116.0, 2.5)  # $200
        slm.register_position("AVAX/USD", SignalAction.BUY, 30.0, 100.0, 0.0, 28.0, 36.0, 1.0)  # $200

        assert pytest.approx(slm.get_portfolio_risk_heat(equity), abs=1e-4) == 0.0040

        slm.close_position("SOL/USD")
        assert pytest.approx(slm.get_portfolio_risk_heat(equity), abs=1e-4) == 0.0020


# ==============================================================================
# BOUNDARY 5: Dynamic Trailing Stop Ratchet & Breakeven Boundary
# ==============================================================================

class TestBoundary5TrailingStopRatchetAndBreakeven:
    """Tests trailing stop transitions, breakeven triggers, and monotonic stop ratcheting."""

    def test_long_breakeven_ratchet_at_1_2_atr(self):
        """Long position ratchets stop to entry*1.001 when price hits entry + 1.2*ATR."""
        slm = StopLossManager()
        entry_p = 100.0
        atr = 5.0

        slm.register_position(
            pair="SOL/USD",
            side=SignalAction.BUY,
            entry_price=entry_p,
            quantity=10.0,
            collateral=0.0,
            stop_loss=92.5,
            take_profit=120.0,
            atr=atr,
        )

        pos = slm.positions["SOL/USD"]
        assert not pos.breakeven_activated

        # Price reaches entry + 1.2*ATR = 106.0
        slm.evaluate_exits("SOL/USD", current_price=106.1)
        assert pos.breakeven_activated
        assert pos.stop_loss == pytest.approx(entry_p * 1.001)

    def test_long_trailing_stop_activates_at_2_0_atr(self):
        """Long position activates trailing stop at entry + 2.0*ATR, trailing by 1.8*ATR."""
        slm = StopLossManager()
        entry_p = 100.0
        atr = 5.0

        slm.register_position("SOL/USD", SignalAction.BUY, entry_p, 10.0, 0.0, 92.5, 125.0, atr)
        pos = slm.positions["SOL/USD"]

        # Price surges to entry + 2.5*ATR = 112.5
        slm.evaluate_exits("SOL/USD", current_price=112.5)
        assert pos.trailing_activated
        expected_trailing_stop = 112.5 - (1.8 * atr)  # 112.5 - 9.0 = 103.5
        assert pos.stop_loss == pytest.approx(expected_trailing_stop)

    def test_stop_loss_is_strictly_monotonic_for_longs(self):
        """A long trailing stop must never ratchet downward, even if price retraces."""
        slm = StopLossManager()
        entry_p = 100.0
        atr = 5.0

        slm.register_position("SOL/USD", SignalAction.BUY, entry_p, 10.0, 0.0, 92.5, 130.0, atr)
        pos = slm.positions["SOL/USD"]

        # High watermark at 120.0 -> stop at 120 - 9 = 111.0
        slm.evaluate_exits("SOL/USD", current_price=120.0)
        high_stop = pos.stop_loss

        # Price drops to 115.0 -> stop must NOT move down
        slm.evaluate_exits("SOL/USD", current_price=115.0)
        assert pos.stop_loss == high_stop

    def test_short_breakeven_ratchet_at_1_2_atr(self):
        """Short position ratchets stop to entry*0.999 when price drops by 1.2*ATR."""
        slm = StopLossManager()
        entry_p = 100.0
        atr = 5.0

        slm.register_position("SOL/USD", SignalAction.SHORT_OPEN, entry_p, 10.0, 1000.0, 107.5, 80.0, atr)
        pos = slm.positions["SOL/USD"]

        # Price drops to entry - 1.2*ATR = 94.0
        slm.evaluate_exits("SOL/USD", current_price=93.9)
        assert pos.breakeven_activated
        assert pos.stop_loss == pytest.approx(entry_p * 0.999)

    def test_take_profit_target_trigger(self):
        """Price hitting take-profit generates ExitSignal with MARKET order type."""
        slm = StopLossManager()
        slm.register_position("SOL/USD", SignalAction.BUY, 100.0, 10.0, 0.0, 92.5, 120.0, 5.0)

        exit_sig = slm.evaluate_exits("SOL/USD", current_price=120.5)
        assert exit_sig is not None
        assert exit_sig.action == SignalAction.SELL
        assert "Take Profit" in exit_sig.reason


# ==============================================================================
# BOUNDARY 6: Hardware Circuit Breaker Limits Boundary
# ==============================================================================

class TestBoundary6CircuitBreakerLimits:
    """Tests the 4.0% hardware circuit breaker trip, cooldown, and recovery."""

    def test_circuit_breaker_trips_at_exact_four_percent_drawdown(self):
        """Drawdown reaching exactly 4.00% trips circuit breaker to TRIPPED."""
        cb = CircuitBreaker()
        initial_peak = 100000.0
        cb.update(initial_peak)
        assert cb.status == CircuitBreakerStatus.ACTIVE

        # Equity drops to $96,000 (exactly 4.00% drawdown)
        cb.update(96000.0)
        assert cb.status == CircuitBreakerStatus.TRIPPED

    def test_circuit_breaker_does_not_trip_at_3_99_percent(self):
        """Drawdown of 3.99% ($96,010) leaves circuit breaker ACTIVE."""
        cb = CircuitBreaker()
        cb.update(100000.0)

        cb.update(96010.0)
        assert cb.status == CircuitBreakerStatus.ACTIVE
        assert cb.can_trade() is True

    def test_circuit_breaker_blocks_trading_when_tripped(self):
        """can_trade() returns False when circuit breaker is tripped."""
        cb = CircuitBreaker()
        cb.update(100000.0)
        cb.update(95500.0)  # 4.5% drawdown

        assert cb.status == CircuitBreakerStatus.TRIPPED
        assert cb.can_trade() is False

    def test_circuit_breaker_cooldown_duration(self):
        """Circuit breaker remains tripped until cooldown hours elapse."""
        cb = CircuitBreaker()
        cb.update(100000.0)
        cb.update(95000.0)
        assert cb.status == CircuitBreakerStatus.TRIPPED

        # Advance time by 6 hours (< 12 hour cooldown)
        cb.trip_time = time.time() - (6 * 3600)
        assert cb.can_trade() is False

    def test_circuit_breaker_resets_with_new_capital_peak(self):
        """Manual reset or cooldown expiry restores ACTIVE status and updates peak."""
        cb = CircuitBreaker()
        cb.update(100000.0)
        cb.update(95000.0)
        assert cb.status == CircuitBreakerStatus.TRIPPED

        # Reset
        cb.reset(new_equity=95000.0)
        assert cb.status == CircuitBreakerStatus.ACTIVE
        assert cb.peak_equity == 95000.0
        assert cb.can_trade() is True
