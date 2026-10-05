"""
Unit tests for PositionSizer, StopLossManager, and CircuitBreaker.
"""
import pytest
from risk.position_sizer import PositionSizer
from risk.stop_loss_manager import StopLossManager
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus
from strategy.signals import TradeSignal, SignalAction


def test_position_sizer():
    """Verifies that ATR volatility parity sizing satisfies risk and cash constraints."""
    sizer = PositionSizer()

    # 1. Normal valid BUY signal
    signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=100.0,
        stop_loss_price=90.0,  # $10 stop distance
        take_profit_price=120.0,
        atr=6.67,
        reason="Test",
    )

    # Portfolio: $100k equity, $80k free cash, 0 active positions
    res = sizer.calculate_size(signal, portfolio_equity=100000.0, free_usd=80000.0, current_open_positions_count=0)
    assert res.allowed is True
    from config.trading_config import trading_config
    target_risk = 100000.0 * trading_config.MAX_RISK_PER_TRADE_PCT
    expected_qty = target_risk / 10.0
    assert res.quantity == pytest.approx(expected_qty, abs=0.1)
    assert res.risk_usd == pytest.approx(target_risk, abs=10.0)

    # 2. Position limit behavior
    # Baseline/fallback: blocks when dynamic concurrency is disabled
    trading_config.DYNAMIC_CONCURRENCY_ENABLED = False
    res_blocked = sizer.calculate_size(
        signal,
        portfolio_equity=100000.0,
        free_usd=80000.0,
        current_open_positions_count=trading_config.MAX_CONCURRENT_POSITIONS,
    )
    assert res_blocked.allowed is False
    assert "Position limit reached" in res_blocked.reason

    # Dynamic concurrency enabled: removes hard-cap behavior
    trading_config.DYNAMIC_CONCURRENCY_ENABLED = True
    res_allowed_dynamic = sizer.calculate_size(
        signal,
        portfolio_equity=100000.0,
        free_usd=80000.0,
        current_open_positions_count=trading_config.MAX_CONCURRENT_POSITIONS,
    )
    assert res_allowed_dynamic.allowed is True

    # 3. Block if free cash buffer would breach 25% reserve
    res_no_cash = sizer.calculate_size(signal, portfolio_equity=100000.0, free_usd=25050.0, current_open_positions_count=1)
    assert res_no_cash.allowed is False
    assert "Insufficient free cash" in res_no_cash.reason


def test_stop_loss_manager_trailing_ratchet():
    """Verifies that dynamic trailing stops ratchet up on winning trades and never move down."""
    mgr = StopLossManager()
    pair = "BTC/USD"
    atr = 1000.0

    # Entry at $80,000, initial stop at $78,500 (1.5x ATR away), target at $83,000
    mgr.register_position(
        pair=pair,
        side=SignalAction.BUY,
        entry_price=80000.0,
        quantity=0.1,
        collateral=0.0,
        stop_loss=78500.0,
        take_profit=83000.0,
        atr=atr,
    )

    # Price moves to $81,000 (+1.0x ATR) -> Not enough to activate trailing stop yet
    exit_sig = mgr.update_price(pair, current_price=81000.0)
    assert exit_sig is None
    assert mgr.positions[pair].stop_loss == 78500.0

    # Price pumps to $82,000 (+2.0x ATR) -> Trailing stop activates!
    # New stop should be 82,000 - (1.8 * 1000) = 80,200 (locked in +$200 profit with wide cushion)
    exit_sig = mgr.update_price(pair, current_price=82000.0)
    assert exit_sig is None
    assert mgr.positions[pair].trailing_activated is True
    assert mgr.positions[pair].stop_loss == 80200.0

    # Price pulls back to $81,500 -> Stop must NOT move down!
    mgr.update_price(pair, current_price=81500.0)
    assert mgr.positions[pair].stop_loss == 80200.0

    # Price drops to $80,100 -> Hits trailing stop!
    exit_sig = mgr.update_price(pair, current_price=80100.0)
    assert exit_sig is not None
    assert exit_sig.action == SignalAction.SELL
    assert exit_sig.order_type == "MARKET"
    assert "Trailing Stop Loss Hit" in exit_sig.reason


def test_circuit_breaker():
    """Verifies that the circuit breaker trips at 4% drawdown from peak equity."""
    cb = CircuitBreaker(max_drawdown_pct=0.04, cooldown_hours=12, initial_equity=100000.0)
    assert cb.is_trading_allowed() is True

    # Portfolio grows to $110,000
    status = cb.update_equity(110000.0)
    assert status == CircuitBreakerStatus.NORMAL
    assert cb.peak_equity == 110000.0

    # Portfolio dips to $107,000 (2.7% drawdown from $110k) -> Within tolerance
    status = cb.update_equity(107000.0)
    assert status == CircuitBreakerStatus.NORMAL
    assert cb.is_trading_allowed() is True

    # Portfolio drops to $105,000 (4.5% drawdown from $110k peak) -> Breaches 4.0%!
    status = cb.update_equity(105000.0)
    assert status == CircuitBreakerStatus.TRIPPED
    assert cb.is_trading_allowed() is False

    # Acknowledge liquidation -> transitions to COOLDOWN
    cb.acknowledge_liquidation()
    assert cb.status == CircuitBreakerStatus.COOLDOWN
    assert cb.is_trading_allowed() is False
