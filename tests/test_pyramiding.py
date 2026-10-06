"""
Unit tests for Trend Pyramiding / Scale-In on Winners.
Verifies mathematical invariants:
1. Scale-in eligibility requires breakeven or trailing stop (Tranche 1 is risk-free).
2. Minimum profit threshold (>= 1.5x ATR) required before pouring more capital.
3. Max pyramid tranches (MAX_PYRAMID_TRANCHES = 1) strictly enforced.
4. Blended entry price and stop ratchets are mathematically consistent.
5. Position sizer enforces cash reserve and per-coin concentration invariants.
"""
import pytest
from config.trading_config import trading_config
from strategy.signals import SignalAction
from risk.stop_loss_manager import StopLossManager
from risk.position_sizer import PositionSizer


@pytest.fixture(autouse=True)
def reset_config():
    """Ensure trading_config is cleanly restored after each test."""
    trading_config.reset_defaults()
    yield
    trading_config.reset_defaults()


def test_pyramiding_disabled_by_default():
    slm = StopLossManager()
    slm.register_position(
        pair="BTC/USD",
        side=SignalAction.BUY,
        entry_price=90000.0,
        quantity=0.1,
        collateral=0.0,
        stop_loss=88000.0,
        take_profit=95000.0,
        atr=1000.0,
    )
    # Even if in huge profit, eligibility returns None when PYRAMIDING_ENABLED is False
    candidate = slm.check_pyramid_eligibility("BTC/USD", current_price=93000.0)
    assert candidate is None


def test_pyramiding_requires_breakeven_or_trailing():
    trading_config.PYRAMIDING_ENABLED = True
    slm = StopLossManager()
    slm.register_position(
        pair="BTC/USD",
        side=SignalAction.BUY,
        entry_price=90000.0,
        quantity=0.1,
        collateral=0.0,
        stop_loss=88000.0,
        take_profit=95000.0,
        atr=1000.0,
    )
    pos = slm.positions["BTC/USD"]
    # Not yet breakeven
    pos.breakeven_activated = False
    pos.trailing_activated = False

    candidate = slm.check_pyramid_eligibility("BTC/USD", current_price=92000.0)
    assert candidate is None


def test_pyramiding_requires_profit_hurdle():
    trading_config.PYRAMIDING_ENABLED = True
    trading_config.PYRAMIDING_PROFIT_ATR_MULT = 1.5
    slm = StopLossManager()
    slm.register_position(
        pair="BTC/USD",
        side=SignalAction.BUY,
        entry_price=90000.0,
        quantity=0.1,
        collateral=0.0,
        stop_loss=90090.0,
        take_profit=95000.0,
        atr=1000.0,
    )
    pos = slm.positions["BTC/USD"]
    pos.breakeven_activated = True

    # Profit is only 1.0x ATR ($91,000 - $90,000 = $1,000 < 1.5 * $1,000)
    candidate = slm.check_pyramid_eligibility("BTC/USD", current_price=91000.0)
    assert candidate is None

    # Profit is 1.6x ATR ($91,600 - $90,000 = $1,600 >= 1.5 * $1,000)
    candidate = slm.check_pyramid_eligibility("BTC/USD", current_price=91600.0)
    assert candidate is not None
    assert candidate.pair == "BTC/USD"
    assert candidate.side == SignalAction.BUY
    assert candidate.added_quantity == pytest.approx(0.05)  # 50% scale


def test_pyramiding_long_blended_entry_and_stop_ratchet():
    trading_config.PYRAMIDING_ENABLED = True
    slm = StopLossManager()
    slm.register_position(
        pair="ETH/USD",
        side=SignalAction.BUY,
        entry_price=2500.0,
        quantity=2.0,
        collateral=0.0,
        stop_loss=2502.5,  # Breakeven stop locked in
        take_profit=2700.0,
        atr=50.0,
    )
    pos = slm.positions["ETH/USD"]
    pos.breakeven_activated = True

    # Current price is 2600 (+2.0 ATR)
    candidate = slm.check_pyramid_eligibility("ETH/USD", current_price=2600.0)
    assert candidate is not None

    # Add tranche: +1.0 ETH @ $2,600
    success = slm.add_pyramid_tranche(
        pair="ETH/USD",
        added_qty=candidate.added_quantity,
        fill_price=2600.0,
        new_stop_loss=candidate.new_stop_loss,
    )
    assert success is True

    # Total quantity: 2.0 + 1.0 = 3.0
    assert pos.quantity == pytest.approx(3.0)
    # Blended entry: (2 * 2500 + 1 * 2600) / 3 = 7600 / 3 = 2533.3333
    assert pos.entry_price == pytest.approx(2533.3333, rel=1e-4)
    # Scale in count incremented
    assert pos.scale_in_count == 1
    # Stop loss ratcheted
    assert pos.stop_loss >= 2502.5


def test_pyramiding_short_blended_entry():
    trading_config.PYRAMIDING_ENABLED = True
    slm = StopLossManager()
    slm.register_position(
        pair="SOL/USD",
        side=SignalAction.SHORT_OPEN,
        entry_price=150.0,
        quantity=10.0,
        collateral=1500.0,
        stop_loss=149.85,  # Breakeven stop locked in
        take_profit=130.0,
        atr=5.0,
    )
    pos = slm.positions["SOL/USD"]
    pos.breakeven_activated = True

    # Price dropped to 140.0 (+2.0 ATR profit on short)
    candidate = slm.check_pyramid_eligibility("SOL/USD", current_price=140.0)
    assert candidate is not None
    assert candidate.side == SignalAction.SHORT_OPEN

    # Add short tranche: +5.0 SOL @ $140, +$700 collateral
    success = slm.add_pyramid_tranche(
        pair="SOL/USD",
        added_qty=5.0,
        fill_price=140.0,
        added_collateral=700.0,
        new_stop_loss=candidate.new_stop_loss,
    )
    assert success is True
    assert pos.quantity == pytest.approx(15.0)
    # Blended entry: (10 * 150 + 5 * 140) / 15 = 2200 / 15 = 146.6667
    assert pos.entry_price == pytest.approx(146.6667, rel=1e-4)
    assert pos.collateral == pytest.approx(2200.0)
    assert pos.scale_in_count == 1


def test_max_pyramid_tranches_enforced():
    trading_config.PYRAMIDING_ENABLED = True
    trading_config.MAX_PYRAMID_TRANCHES = 1
    slm = StopLossManager()
    slm.register_position(
        pair="AVAX/USD",
        side=SignalAction.BUY,
        entry_price=30.0,
        quantity=100.0,
        collateral=0.0,
        stop_loss=30.1,
        take_profit=40.0,
        atr=1.0,
    )
    pos = slm.positions["AVAX/USD"]
    pos.breakeven_activated = True

    # Tranche 1
    candidate1 = slm.check_pyramid_eligibility("AVAX/USD", current_price=32.0)
    assert candidate1 is not None
    slm.add_pyramid_tranche("AVAX/USD", candidate1.added_quantity, 32.0)

    # Tranche 2 should be rejected because MAX_PYRAMID_TRANCHES = 1
    candidate2 = slm.check_pyramid_eligibility("AVAX/USD", current_price=34.0)
    assert candidate2 is None


def test_position_sizer_validates_cash_buffer_for_pyramiding():
    sizer = PositionSizer()
    trading_config.MIN_CASH_BUFFER_PCT = 0.20  # $20,000 buffer on $100k
    equity = 100000.0
    free_usd = 20500.0  # Only $500 available above the $20,000 buffer

    res = sizer.validate_pyramid_order(
        pair="BTC/USD",
        action=SignalAction.BUY,
        added_quantity=0.1,  # 0.1 * $90,000 = $9,000 (exceeds $500)
        current_price=90000.0,
        proposed_stop=88000.0,
        portfolio_equity=equity,
        free_usd=free_usd,
        current_position_capital=5000.0,
    )
    # Sizer should clamp capital to available cash ($500)
    assert res.allowed is True
    assert res.estimated_capital <= 500.0


def test_position_sizer_rejects_when_cash_buffer_breached():
    sizer = PositionSizer()
    trading_config.MIN_CASH_BUFFER_PCT = 0.20  # $20,000 buffer
    equity = 100000.0
    free_usd = 19500.0  # Already below minimum cash buffer

    res = sizer.validate_pyramid_order(
        pair="BTC/USD",
        action=SignalAction.BUY,
        added_quantity=0.1,
        current_price=90000.0,
        proposed_stop=88000.0,
        portfolio_equity=equity,
        free_usd=free_usd,
        current_position_capital=5000.0,
    )
    assert res.allowed is False
    assert "cash reserve" in res.reason.lower()


def test_position_sizer_validates_coin_allocation_cap():
    sizer = PositionSizer()
    trading_config.MAX_ALLOCATION_PER_COIN_PCT = 0.25  # Max $25,000 per coin
    equity = 100000.0
    free_usd = 50000.0

    res = sizer.validate_pyramid_order(
        pair="BTC/USD",
        action=SignalAction.BUY,
        added_quantity=0.1,
        current_price=90000.0,
        proposed_stop=88000.0,
        portfolio_equity=equity,
        free_usd=free_usd,
        current_position_capital=24995.0,  # Only $5 headroom left (< $10 min order)
    )
    assert res.allowed is False
    assert "allocation limit reached" in res.reason.lower()


def test_apply_profile_pyramiding_optimal():
    trading_config.apply_profile("pyramiding_optimal")
    assert trading_config.PYRAMIDING_ENABLED is True
    assert trading_config.PYRAMIDING_PROFIT_ATR_MULT == 1.5
    assert trading_config.PYRAMIDING_TRANCHE_SCALE == 0.50
    assert trading_config.MAX_PYRAMID_TRANCHES == 1
    assert trading_config.MAX_ALLOCATION_PER_COIN_PCT == 0.30
    assert trading_config.MAX_HOLDING_BARS == 32
