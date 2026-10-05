"""
Automated Invariant Test Suite for Dynamic Portfolio Concurrency & Risk Engine (Milestone M2).
Verifies:
1. Dynamic Concurrency Scaling (2 assets and 6 assets)
2. Cumulative Risk Heat Invariant (\\sum Risk_i <= 1.0%)
3. Breakeven Trailing Stop Frees Risk Heat
4. Free Cash Reserve Invariant (>= 25% of equity)
5. Simultaneous Stop-Out Drawdown Guarantee (Drawdown <= 1.075% << 4.0% limit)
6. Dedicated BTC Trend Velocity Factor & Portfolio Allocation
7. Regime-Adaptive Risk Contraction & 100% Cash Defense in Bear
"""
import pytest
import numpy as np
import pandas as pd

from config.trading_config import trading_config
from risk.position_sizer import PositionSizer, OrderSizeResult
from risk.stop_loss_manager import StopLossManager
from strategy.signals import TradeSignal, SignalAction
from strategy.regime_detector import MarketRegime
from strategy.linear_factor_model import CryptoLinearFactorModel


def make_synth_trend_candles(n: int, slope: float, vol: float = 0.005, base_price: float = 60000.0) -> pd.DataFrame:
    """Generate synthetic OHLCV candles with controlled drift and volatility."""
    np.random.seed(42)
    returns = np.random.normal(slope, vol, n)
    prices = base_price * np.cumprod(1.0 + returns)
    timestamps = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")

    highs = prices * (1.0 + np.abs(np.random.normal(0.002, 0.001, n)))
    lows = prices * (1.0 - np.abs(np.random.normal(0.002, 0.001, n)))
    opens = prices * (1.0 + np.random.normal(0.0, 0.001, n))
    volumes = np.random.uniform(500, 2000, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    })


def test_dynamic_concurrency_scaling_2_assets():
    """
    GIVEN: Equity = $100k, Cash = $80k, Bull regime, 2 assets qualify with convictions 0.8 and 0.6.
    EXPECTED: Concurrency = 2. Total risk heat <= 1.0%.
    Risk split proportionally: Asset 1 gets ~0.50%, Asset 2 gets ~0.43%. Total heat ~0.93% <= 1.0%.
    """
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    sig1 = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=100.0,
        stop_loss_price=90.0,  # $10 stop distance (10%)
        take_profit_price=120.0,
        atr=5.0,
        reason="Bull Momentum",
    )
    sig2 = TradeSignal(
        pair="ETH/USD",
        action=SignalAction.BUY,
        conviction=0.6,
        entry_price=2500.0,
        stop_loss_price=2375.0,  # $125 stop distance (5%)
        take_profit_price=2750.0,
        atr=60.0,
        reason="Bull Momentum",
    )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[sig1, sig2],
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    assert len(results) == 2
    assert results[0].allowed is True
    assert results[1].allowed is True

    # Check risk heat per asset
    risk_heat_1 = results[0].risk_usd / equity
    risk_heat_2 = results[1].risk_usd / equity

    # Asset 1 capped at single-asset risk ceiling (0.50% = $500)
    assert risk_heat_1 <= trading_config.MAX_RISK_PER_ASSET_PCT + 1e-4
    assert results[0].risk_usd == pytest.approx(500.0, abs=15.0)

    # Asset 2 receives proportional share: 0.6 / 1.4 * 1.0% ~ 0.4286% ($428.60)
    assert risk_heat_2 <= trading_config.MAX_RISK_PER_ASSET_PCT + 1e-4
    assert results[1].risk_usd == pytest.approx(428.6, abs=15.0)

    # Total cumulative heat invariant <= 1.0% ($1,000)
    total_risk_heat = risk_heat_1 + risk_heat_2
    assert total_risk_heat <= trading_config.MAX_PORTFOLIO_HEAT_PCT + 1e-4
    assert total_risk_heat == pytest.approx(0.00928, abs=0.0005)


def test_dynamic_concurrency_scaling_6_assets():
    """
    GIVEN: Bull regime, 6 assets qualify with high conviction.
    EXPECTED: All 6 assets receive non-zero allocations. Concurrency scales to 6
    without being capped at legacy 3. Total cumulative risk heat <= 1.0%.
    """
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "NEAR/USD", "SUI/USD"]
    prices = [60000.0, 2500.0, 100.0, 30.0, 5.0, 2.0]
    signals = []

    for p, pr in zip(pairs, prices):
        signals.append(
            TradeSignal(
                pair=p,
                action=SignalAction.BUY,
                conviction=0.85,
                entry_price=pr,
                stop_loss_price=pr * 0.95,  # 5% stop
                take_profit_price=pr * 1.10,
                atr=pr * 0.02,
                reason="Impulse Breakout",
            )
        )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=signals,
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    # All 6 must be allowed - no arbitrary 3-slot hard limit!
    assert len(results) == 6
    for res in results:
        assert res.allowed is True
        assert res.quantity > 0
        assert res.risk_usd > 0

    total_risk_usd = sum(r.risk_usd for r in results)
    total_heat = total_risk_usd / equity

    # Total heat strictly bounded to <= 1.0%
    assert total_heat <= trading_config.MAX_PORTFOLIO_HEAT_PCT + 1e-4
    # Each asset received approx 1.0% / 6 ~ 0.167% ($166.67)
    for res in results:
        assert res.risk_usd == pytest.approx(166.67, abs=15.0)


def test_cumulative_risk_heat_invariant():
    """
    GIVEN: 3 positions already open carrying 0.60% active risk heat.
    GIVEN: 3 new assets qualify.
    EXPECTED: New assets receive at most 1.00% - 0.60% = 0.40% total risk heat.
    Under no condition can total portfolio heat exceed 1.00%.
    """
    sizer = PositionSizer()
    equity = 100000.0
    cash = 60000.0

    # 3 positions already open carrying 0.20% ($200) risk each = $600 total (0.60% heat)
    open_positions = [
        {"pair": "SOL/USD", "entry_price": 100.0, "stop_loss": 90.0, "quantity": 20.0, "side": "BUY"},
        {"pair": "ETH/USD", "entry_price": 2500.0, "stop_loss": 2375.0, "quantity": 1.6, "side": "BUY"},
        {"pair": "AVAX/USD", "entry_price": 30.0, "stop_loss": 27.0, "quantity": 66.67, "side": "BUY"},
    ]

    new_signals = [
        TradeSignal(pair="NEAR/USD", action=SignalAction.BUY, conviction=0.9, entry_price=5.0, stop_loss_price=4.5, take_profit_price=6.0, atr=0.2, reason="Signal"),
        TradeSignal(pair="SUI/USD", action=SignalAction.BUY, conviction=0.9, entry_price=2.0, stop_loss_price=1.8, take_profit_price=2.4, atr=0.1, reason="Signal"),
        TradeSignal(pair="LINK/USD", action=SignalAction.BUY, conviction=0.9, entry_price=15.0, stop_loss_price=13.5, take_profit_price=18.0, atr=0.6, reason="Signal"),
    ]

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=new_signals,
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=open_positions,
        regime=MarketRegime.BULL,
    )

    new_risk_usd = sum(r.risk_usd for r in results if r.allowed)
    new_risk_heat = new_risk_usd / equity

    # Available heat was 1.0% - 0.60% = 0.40% ($400)
    assert new_risk_heat <= 0.0040 + 1e-4

    total_portfolio_heat = 0.0060 + new_risk_heat
    assert total_portfolio_heat <= trading_config.MAX_PORTFOLIO_HEAT_PCT + 1e-4


def test_breakeven_stop_frees_risk_heat():
    """
    GIVEN: A position's stop ratchets to breakeven (P_stop >= P_entry).
    EXPECTED: slm.get_portfolio_risk_heat() reports 0.0% risk for that position,
    immediately unlocking its risk capacity for new qualifying assets.
    """
    slm = StopLossManager()
    equity = 100000.0
    pair = "BTC/USD"

    # Open position risking $500 (0.50% heat): 0.5 BTC, entry $60,000, stop $59,000
    slm.register_position(
        pair=pair,
        side=SignalAction.BUY,
        entry_price=60000.0,
        quantity=0.5,
        collateral=0.0,
        stop_loss=59000.0,  # $1,000 risk per BTC * 0.5 = $500 risk
        take_profit=64000.0,
        atr=800.0,
    )

    initial_heat = slm.get_portfolio_risk_heat(portfolio_equity=equity)
    assert initial_heat == pytest.approx(0.0050, abs=1e-5)

    # Position ratchets to breakeven (+10 bps above entry)
    slm.positions[pair].stop_loss = 60060.0
    slm.positions[pair].breakeven_activated = True

    ratcheted_heat = slm.get_portfolio_risk_heat(portfolio_equity=equity)
    # Downside risk is now $0 -> heat consumed is 0.0!
    assert ratcheted_heat == 0.0

    # With 0.0% open heat, a new asset qualifies and captures full risk capacity
    sizer = PositionSizer()
    new_signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.9,
        entry_price=100.0,
        stop_loss_price=90.0,
        take_profit_price=120.0,
        atr=5.0,
        reason="Fresh allocation",
    )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[new_signal],
        portfolio_equity=equity,
        free_usd=80000.0,
        current_open_positions=slm,
        regime=MarketRegime.BULL,
    )

    assert len(results) == 1
    assert results[0].allowed is True
    # Can now capture up to full 0.50% per-asset risk ceiling
    assert results[0].risk_usd == pytest.approx(500.0, abs=10.0)


def test_cash_reserve_invariant_25_pct():
    """
    GIVEN: Free cash is tight ($30k on $100k equity, only $5k above 25% reserve).
    GIVEN: Candidate signals requesting high notional capital.
    EXPECTED: Total capital deployed never exceeds available trading cash ($5,000).
    Free cash remaining after allocations is guaranteed >= 25% ($25,000).
    """
    sizer = PositionSizer()
    equity = 100000.0
    tight_free_usd = 30000.0  # $30k free USD
    min_reserve = equity * trading_config.MIN_CASH_BUFFER_PCT  # $25k

    # Signals with very tight stops that demand substantial capital
    sig1 = TradeSignal(pair="BTC/USD", action=SignalAction.BUY, conviction=0.8, entry_price=60000.0, stop_loss_price=59400.0, take_profit_price=62000.0, atr=400.0, reason="tight")
    sig2 = TradeSignal(pair="ETH/USD", action=SignalAction.BUY, conviction=0.8, entry_price=2500.0, stop_loss_price=2475.0, take_profit_price=2600.0, atr=20.0, reason="tight")

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[sig1, sig2],
        portfolio_equity=equity,
        free_usd=tight_free_usd,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    allowed_results = [r for r in results if r.allowed]
    total_capital_deployed = sum(r.estimated_capital for r in allowed_results)

    # Deployed capital must not exceed $5,000 ($30,000 - $25,000)
    max_deployable = tight_free_usd - min_reserve
    assert total_capital_deployed <= max_deployable + 1.0

    # Remaining cash invariant
    remaining_cash = tight_free_usd - total_capital_deployed
    assert remaining_cash >= min_reserve - 1.0


def test_simultaneous_stop_out_drawdown_guarantee():
    """
    Mathematical and empirical proof that simultaneous gap stop-outs across all held positions
    can NEVER exceed 1.075% portfolio drawdown, maintaining absolute immunity to the 4.0% circuit breaker.
    """
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    # Size 5 diverse assets in Bull regime
    pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "NEAR/USD"]
    prices = [60000.0, 2500.0, 100.0, 30.0, 5.0]
    stop_distances_pct = [0.03, 0.04, 0.05, 0.06, 0.05]

    signals = [
        TradeSignal(
            pair=p,
            action=SignalAction.BUY,
            conviction=0.8,
            entry_price=pr,
            stop_loss_price=pr * (1.0 - sd),
            take_profit_price=pr * (1.0 + sd * 2.0),
            atr=pr * (sd / 2.5),
            reason="Test",
        )
        for p, pr, sd in zip(pairs, prices, stop_distances_pct)
    ]

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=signals,
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    # Simulate catastrophic simultaneous stop-out with 10 bps (0.10%) adverse gap slippage
    taker_slippage = 0.0010
    total_dollar_loss = 0.0

    for res, sig in zip(results, signals):
        assert res.allowed is True
        qty = res.quantity
        entry_p = sig.entry_price
        stop_p = sig.stop_loss_price
        # Fill price is 10 bps worse than stop price
        fill_p = stop_p * (1.0 - taker_slippage)
        loss = qty * (entry_p - fill_p)
        total_dollar_loss += loss

    drawdown_pct = (total_dollar_loss / equity) * 100.0

    # MATHEMATICAL PROOF INVARIANT:
    # Under 1.0% risk budget and 75% max capital with 10 bps slippage:
    # Max loss <= 1.0% + (0.10% * 75%) = 1.0% + 0.075% = 1.075%
    assert drawdown_pct <= 1.075 + 1e-4
    # Well below the 4.0% competition disqualification circuit breaker!
    assert drawdown_pct < 4.0
    safety_margin_pct = 4.0 - drawdown_pct
    assert safety_margin_pct >= 2.90  # At least 2.90% safety margin


def test_btc_trend_velocity_and_allocation():
    """
    GIVEN: BTC undergoes strong trend expansion (positive drift, high velocity).
    EXPECTED: BTC produces a high-conviction signal and captures portfolio allocation.
    """
    model = CryptoLinearFactorModel()
    btc_candles = make_synth_trend_candles(100, slope=0.003, vol=0.004, base_price=65000.0)

    # 1. Verify velocity score calculation
    v_btc, m_btc = model.compute_btc_velocity_score(btc_candles)
    assert v_btc > 0.0
    assert m_btc > 0.20

    # 2. Verify dedicated BTC signal generation
    btc_signal = model.compute_btc_signal(btc_candles)
    assert btc_signal is not None
    assert btc_signal.pair == "BTC/USD"
    assert btc_signal.action == SignalAction.BUY
    assert btc_signal.conviction >= 0.50
    assert btc_signal.stop_loss_price < btc_signal.entry_price

    # 3. Verify BTC captures portfolio allocation alongside altcoins
    sizer = PositionSizer()
    alt_signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.75,
        entry_price=100.0,
        stop_loss_price=95.0,
        take_profit_price=110.0,
        atr=3.0,
        reason="Alt Breakout",
    )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[btc_signal, alt_signal],
        portfolio_equity=100000.0,
        free_usd=80000.0,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    btc_res = next((r for r in results if r.pair == "BTC/USD"), None)
    assert btc_res is not None
    assert btc_res.allowed is True
    assert btc_res.quantity > 0
    assert btc_res.risk_usd > 0
    # Confirms BTC actively competes and receives significant capital allocation
    assert btc_res.estimated_capital >= 1000.0


def test_regime_contraction_to_cash_defense():
    """
    GIVEN: Market transitions to CHOP and BEAR.
    EXPECTED:
    - In CHOP: risk budget contracts to <= 0.40%, conviction hurdle >= 0.45.
    - In BEAR: long entries completely prohibited, preserving 100% cash defense.
    """
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    high_conv_signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.80,
        entry_price=100.0,
        stop_loss_price=95.0,
        take_profit_price=110.0,
        atr=3.0,
        reason="High Conviction",
    )
    low_conv_signal = TradeSignal(
        pair="ETH/USD",
        action=SignalAction.BUY,
        conviction=0.35,  # Below CHOP hurdle of 0.45
        entry_price=2500.0,
        stop_loss_price=2400.0,
        take_profit_price=2700.0,
        atr=50.0,
        reason="Low Conviction",
    )

    # 1. CHOP REGIME
    chop_results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[high_conv_signal, low_conv_signal],
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.CHOP,
    )

    # High conviction allowed within contracted 0.40% ceiling
    chop_sol = next(r for r in chop_results if r.pair == "SOL/USD")
    assert chop_sol.allowed is True
    assert chop_sol.risk_usd / equity <= trading_config.CHOP_RISK_HEAT_CEILING + 1e-4

    # Low conviction rejected due to CHOP hurdle
    chop_eth = next(r for r in chop_results if r.pair == "ETH/USD")
    assert chop_eth.allowed is False
    assert "CHOP threshold" in chop_eth.reason

    # 2. BEAR REGIME - 100% CASH DEFENSE
    bear_results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[high_conv_signal],
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BEAR,
    )

    bear_sol = bear_results[0]
    assert bear_sol.allowed is False
    assert "BEAR regime" in bear_sol.reason
    assert bear_sol.risk_usd == 0.0


def test_nan_inf_non_positive_inputs_rejected():
    """Verify that NaN, Inf, or non-positive price/conviction inputs are rejected."""
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    nan_signal = TradeSignal(
        pair="BTC/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=float("nan"),
        stop_loss_price=float("nan"),
        take_profit_price=65000.0,
        atr=500.0,
        reason="Corrupt Signal",
    )
    inf_signal = TradeSignal(
        pair="ETH/USD",
        action=SignalAction.BUY,
        conviction=float("inf"),
        entry_price=2500.0,
        stop_loss_price=2400.0,
        take_profit_price=2700.0,
        atr=50.0,
        reason="Inf Conviction",
    )
    neg_signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=-100.0,
        stop_loss_price=90.0,
        take_profit_price=120.0,
        atr=5.0,
        reason="Negative Price",
    )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[nan_signal, inf_signal, neg_signal],
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    for res in results:
        assert res.allowed is False
        assert res.quantity == 0.0
        assert not np.isnan(res.quantity)
        assert "Invalid" in res.reason


def test_directional_consistency_enforced():
    """Verify that reversed stops (BUY with stop >= entry, SHORT with stop <= entry) are rejected."""
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    reversed_buy = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.85,
        entry_price=100.0,
        stop_loss_price=110.0,  # stop > entry for BUY is reversed!
        take_profit_price=130.0,
        atr=5.0,
        reason="Reversed Buy",
    )
    reversed_short = TradeSignal(
        pair="ETH/USD",
        action=SignalAction.SHORT_OPEN,
        conviction=0.85,
        entry_price=2500.0,
        stop_loss_price=2400.0,  # stop < entry for SHORT is reversed!
        take_profit_price=2200.0,
        atr=50.0,
        reason="Reversed Short",
    )

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=[reversed_buy, reversed_short],
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    for res in results:
        assert res.allowed is False
        assert res.quantity == 0.0
        assert "Directional inconsistency" in res.reason


def test_zero_negative_equity_guard():
    """Verify that zero or negative equity returns empty without ZeroDivisionError."""
    sizer = PositionSizer()
    sig = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=100.0,
        stop_loss_price=90.0,
        take_profit_price=120.0,
        atr=5.0,
        reason="Test",
    )

    res_zero = sizer.calculate_dynamic_portfolio_sizes([sig], portfolio_equity=0.0, free_usd=0.0)
    assert res_zero == []
    assert res_zero == {}
    assert len(res_zero) == 0

    res_neg = sizer.calculate_dynamic_portfolio_sizes([sig], portfolio_equity=-500.0, free_usd=0.0)
    assert res_neg == []
    assert res_neg == {}
    assert len(res_neg) == 0


def test_zero_atr_safe_handling():
    """Verify that StopLossManager with atr=0.0 does not crash with ZeroDivisionError."""
    slm = StopLossManager()
    slm.register_position(
        pair="SOL/USD",
        side=SignalAction.BUY,
        entry_price=100.0,
        quantity=10.0,
        collateral=0.0,
        stop_loss=90.0,
        take_profit=120.0,
        atr=0.0,  # Zero ATR
    )

    # update_price should handle zero ATR safely
    exit_sig = slm.update_price("SOL/USD", current_price=105.0)
    assert exit_sig is None  # Position remains active, no crash

    # Also test evaluate_exits alias
    exit_sig_alias = slm.evaluate_exits("SOL/USD", current_price=105.0)
    assert exit_sig_alias is None


def test_post_rounding_clamp_guarantees_heat_and_cash_invariants():
    """Verify that discrete lot-size rounding can never breach 1.0% heat or 25% cash buffer."""
    sizer = PositionSizer()
    equity = 100000.0
    cash = 80000.0

    # 10 signals sized simultaneously
    pairs = [f"COIN{i}/USD" for i in range(10)]
    prices = [10.0 + i * 2.0 for i in range(10)]
    signals = [
        TradeSignal(
            pair=p,
            action=SignalAction.BUY,
            conviction=0.9,
            entry_price=pr,
            stop_loss_price=pr * 0.95,
            take_profit_price=pr * 1.10,
            atr=pr * 0.02,
            reason="Clamping Test",
        )
        for p, pr in zip(pairs, prices)
    ]

    results = sizer.calculate_dynamic_portfolio_sizes(
        signals=signals,
        portfolio_equity=equity,
        free_usd=cash,
        current_open_positions=[],
        regime=MarketRegime.BULL,
    )

    allowed = [r for r in results if r.allowed]
    total_risk_usd = sum(r.risk_usd for r in allowed)
    total_heat = total_risk_usd / equity
    total_capital = sum(r.estimated_capital for r in allowed)

    # Invariant 1: Heat strictly <= 1.0%
    assert total_heat <= trading_config.MAX_PORTFOLIO_HEAT_PCT + 1e-6

    # Invariant 2: Capital deployed leaves >= 25% cash buffer
    assert (cash - total_capital) >= (equity * trading_config.MIN_CASH_BUFFER_PCT) - 1e-6


def test_order_router_accepts_and_preserves_dynamic_size_result():
    """Verify that OrderRouter.execute_entry uses size_result when provided, preserving dynamic quantities."""
    from unittest.mock import MagicMock
    from execution.order_router import OrderRouter

    mock_client = MagicMock()
    mock_client.place_order.return_value = {
        "Success": True,
        "OrderDetail": {"Price": 100.0, "OrderID": "12345", "CommissionChargeValue": 0.05},
    }
    mock_cm = MagicMock()
    mock_cm.get_latest_bid_ask.return_value = (100.0, 100.1)
    mock_db = MagicMock()
    slm = StopLossManager()
    sizer = PositionSizer()

    router = OrderRouter(
        roostoo_client=mock_client,
        candle_manager=mock_cm,
        position_sizer=sizer,
        stop_loss_manager=slm,
        database=mock_db,
    )

    sig = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.9,
        entry_price=100.0,
        stop_loss_price=90.0,
        take_profit_price=120.0,
        atr=5.0,
        reason="Test",
    )

    custom_size = OrderSizeResult(
        allowed=True,
        pair="SOL/USD",
        action=SignalAction.BUY,
        quantity=12.345,
        collateral=0.0,
        estimated_capital=1234.5,
        risk_usd=123.45,
        reason="Dynamic Partitioned Size",
    )

    success = router.execute_entry(
        signal=sig,
        portfolio_equity=100000.0,
        free_usd=80000.0,
        prefer_maker=True,
        size_result=custom_size,
    )

    assert success is True
    mock_client.place_order.assert_called_once()
    _, kwargs = mock_client.place_order.call_args
    assert kwargs.get("quantity") == 12.345
    assert slm.positions["SOL/USD"].quantity == 12.345

