"""
Unit tests for CarrotQuant Execution Engine (OrderRouter and StateReconciler).
Tests order routing, maker-first limit order logic, stop-loss exits, shorting,
emergency liquidations, and startup crash recovery reconciliation with mocked API calls.
"""
from unittest.mock import MagicMock
import pytest
from pathlib import Path

from execution.order_router import OrderRouter
from execution.reconciler import StateReconciler
from data.candle_manager import CandleManager
from risk.position_sizer import PositionSizer
from risk.stop_loss_manager import StopLossManager, ExitSignal
from storage.db import Database
from strategy.signals import TradeSignal, SignalAction


@pytest.fixture
def mock_setup(tmp_path: Path):
    db_file = tmp_path / "test_exec.db"
    db = Database(db_path=db_file)
    client = MagicMock()
    candle_manager = CandleManager()
    sizer = PositionSizer()
    stop_loss_manager = StopLossManager()
    alerter = MagicMock()

    # Pre-populate candle prices
    candle_manager.latest_ticks["SOL/USD"] = {"price": 150.0, "bid": 149.95, "ask": 150.05}
    candle_manager.latest_ticks["BTC/USD"] = {"price": 65000.0, "bid": 64990.0, "ask": 65010.0}
    candle_manager.latest_ticks["DOGE/USD"] = {"price": 0.15, "bid": 0.149, "ask": 0.151}

    router = OrderRouter(
        roostoo_client=client,
        candle_manager=candle_manager,
        position_sizer=sizer,
        stop_loss_manager=stop_loss_manager,
        database=db,
        email_alerter=alerter,
    )

    reconciler = StateReconciler(
        roostoo_client=client,
        candle_manager=candle_manager,
        stop_loss_manager=stop_loss_manager,
        database=db,
    )

    return {
        "db": db,
        "client": client,
        "candle_manager": candle_manager,
        "sizer": sizer,
        "stop_loss_manager": stop_loss_manager,
        "alerter": alerter,
        "router": router,
        "reconciler": reconciler,
    }


def test_order_router_buy_maker_execution(mock_setup):
    router = mock_setup["router"]
    client = mock_setup["client"]
    db = mock_setup["db"]
    slm = mock_setup["stop_loss_manager"]

    # Mock successful BUY response
    client.place_order.return_value = {
        "Success": True,
        "OrderDetail": {
            "OrderID": 998877,
            "Pair": "SOL/USD",
            "Side": "BUY",
            "Price": 149.95,
            "FilledQty": 10.0,
            "CommissionChargeValue": 0.75,
        },
    }

    signal = TradeSignal(
        pair="SOL/USD",
        action=SignalAction.BUY,
        conviction=0.8,
        entry_price=150.0,
        stop_loss_price=145.0,
        take_profit_price=160.0,
        atr=3.0,
        reason="BULL_EMA_MOMENTUM",
    )

    # Execute maker buy
    success = router.execute_entry(
        signal=signal,
        portfolio_equity=100000.0,
        free_usd=50000.0,
        prefer_maker=True,
    )

    assert success is True
    client.place_order.assert_called_once()
    call_args = client.place_order.call_args[1]
    assert call_args["pair"] == "SOL/USD"
    assert call_args["side"] == "BUY"
    assert call_args["order_type"] == "LIMIT"
    assert pytest.approx(call_args["price"]) == 149.95

    # Check position registration in memory and database
    assert "SOL/USD" in slm.positions
    assert len(db.get_active_positions()) == 1
    trades = db.get_all_trades()
    assert len(trades) == 1
    assert trades.iloc[0]["order_type"] == "LIMIT"


def test_order_router_sell_exit(mock_setup):
    router = mock_setup["router"]
    client = mock_setup["client"]
    db = mock_setup["db"]
    slm = mock_setup["stop_loss_manager"]

    # Register initial position
    slm.register_position(
        pair="SOL/USD",
        side=SignalAction.BUY,
        entry_price=150.0,
        quantity=10.0,
        collateral=0.0,
        stop_loss=145.0,
        take_profit=160.0,
        atr=3.0,
    )
    db.save_active_position(
        pair="SOL/USD",
        side="BUY",
        entry_price=150.0,
        quantity=10.0,
        collateral=0.0,
        stop_loss=145.0,
        take_profit=160.0,
        atr=3.0,
        highest_price=150.0,
        lowest_price=150.0,
    )

    # Mock successful SELL response
    client.place_order.return_value = {
        "Success": True,
        "OrderDetail": {
            "OrderID": 998878,
            "Pair": "SOL/USD",
            "Side": "SELL",
            "Price": 160.0,
            "FilledQty": 10.0,
            "CommissionChargeValue": 1.6,
        },
    }

    exit_sig = ExitSignal(
        pair="SOL/USD",
        action=SignalAction.SELL,
        quantity=10.0,
        collateral=0.0,
        order_type="MARKET",
        reason="Take Profit Reached",
    )

    success = router.execute_exit(exit_sig, current_price=160.0)

    assert success is True
    assert "SOL/USD" not in slm.positions
    assert len(db.get_active_positions()) == 0
    trades = db.get_all_trades()
    assert len(trades) == 1
    assert trades.iloc[0]["side"] == "SELL"
    # Realized PnL: (160 - 150) * 10 = 100.0
    assert pytest.approx(trades.iloc[0]["realized_pnl"]) == 100.0


def test_order_router_short_open_and_close(mock_setup):
    router = mock_setup["router"]
    client = mock_setup["client"]
    db = mock_setup["db"]
    slm = mock_setup["stop_loss_manager"]

    # 1. Short Open
    client.short_open.return_value = {
        "Success": True,
        "ID": 554433,
        "Pair": "DOGE/USD",
        "EntryPrice": 0.15,
        "ShortQty": 20000.0,
        "Collateral": 3000.0,
        "OpenFee": 3.0,
    }

    short_signal = TradeSignal(
        pair="DOGE/USD",
        action=SignalAction.SHORT_OPEN,
        conviction=0.85,
        entry_price=0.15,
        stop_loss_price=0.16,
        take_profit_price=0.13,
        atr=0.005,
        reason="BEAR_BREAKDOWN",
    )

    open_success = router.execute_entry(
        signal=short_signal,
        portfolio_equity=100000.0,
        free_usd=50000.0,
    )

    assert open_success is True
    client.short_open.assert_called_once()
    assert "DOGE/USD" in slm.positions
    assert slm.positions["DOGE/USD"].side == SignalAction.SHORT_OPEN

    # 2. Short Close
    client.short_close.return_value = {
        "Success": True,
        "ClosePrice": 0.13,
        "RealizedPNL": 400.0,
        "CloseFee": 2.6,
    }

    close_exit_sig = ExitSignal(
        pair="DOGE/USD",
        action=SignalAction.SHORT_CLOSE,
        quantity=20000.0,
        collateral=3000.0,
        order_type="MARKET",
        reason="Take Profit Reached",
    )

    close_success = router.execute_exit(close_exit_sig, current_price=0.13)
    assert close_success is True
    assert "DOGE/USD" not in slm.positions
    assert len(db.get_active_positions()) == 0


def test_emergency_liquidation(mock_setup):
    router = mock_setup["router"]
    client = mock_setup["client"]
    slm = mock_setup["stop_loss_manager"]

    slm.register_position("SOL/USD", SignalAction.BUY, 150.0, 10.0, 0.0, 145.0, 160.0, 3.0)
    slm.register_position("DOGE/USD", SignalAction.SHORT_OPEN, 0.15, 20000.0, 3000.0, 0.16, 0.13, 0.005)

    client.place_order.return_value = {"Success": True, "OrderDetail": {"Price": 150.0}}
    client.short_close.return_value = {"Success": True, "ClosePrice": 0.15, "RealizedPNL": 0.0}

    router.emergency_liquidate_all_to_usd()

    assert len(slm.positions) == 0


def test_state_reconciler_on_startup(mock_setup):
    reconciler = mock_setup["reconciler"]
    client = mock_setup["client"]
    slm = mock_setup["stop_loss_manager"]

    # Mock Roostoo live response
    client.get_balance.return_value = {
        "Success": True,
        "Wallet": {
            "USD": {"Free": 70000.0, "Lock": 0.0},
            "BTC": {"Free": 0.2, "Lock": 0.0},       # 0.2 * 65000 = $13,000
            "SOL": {"Free": 50.0, "Lock": 0.0},      # 50 * 150 = $7,500
        },
    }
    client.get_short_positions.return_value = {
        "Success": True,
        "Positions": [
            {
                "Pair": "DOGE/USD",
                "Collateral": 3000.0,
                "ShortQty": 20000.0,
                "EntryPrice": 0.15,
                "PositionValue": 3000.0,
            }
        ],
    }

    total_equity, free_usd = reconciler.reconcile_on_startup()

    assert pytest.approx(free_usd) == 70000.0
    # Total equity = 70000 (USD) + 13000 (BTC) + 7500 (SOL) + 3000 (Short Doge) = 93500.0
    assert pytest.approx(total_equity) == 93500.0
    # Positions registered into memory
    assert "BTC/USD" in slm.positions
    assert "SOL/USD" in slm.positions
    assert "DOGE/USD" in slm.positions
