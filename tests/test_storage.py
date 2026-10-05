"""
Unit tests for CarrotQuant Database module (storage/db.py).
Tests trade logging, portfolio equity snapshots, and active position persistence.
"""
import pytest
from pathlib import Path
from storage.db import Database


def test_database_initialization(tmp_path: Path):
    db_file = tmp_path / "test_carrotquant.db"
    db = Database(db_path=db_file)
    assert db_file.exists()


def test_trade_logging_and_retrieval(tmp_path: Path):
    db_file = tmp_path / "test_carrotquant.db"
    db = Database(db_path=db_file)

    row_id = db.log_trade(
        pair="BTC/USD",
        side="BUY",
        order_type="LIMIT",
        price=65000.0,
        quantity=0.25,
        fee=8.125,
        fee_coin="USD",
        realized_pnl=0.0,
        order_id="123456",
        reason="BULL_EMA_CROSSOVER",
    )
    assert row_id is not None and row_id > 0

    trades_df = db.get_all_trades(limit=10)
    assert len(trades_df) == 1
    assert trades_df.iloc[0]["pair"] == "BTC/USD"
    assert trades_df.iloc[0]["side"] == "BUY"
    assert trades_df.iloc[0]["order_type"] == "LIMIT"
    assert pytest.approx(trades_df.iloc[0]["price"]) == 65000.0
    assert pytest.approx(trades_df.iloc[0]["quantity"]) == 0.25
    assert trades_df.iloc[0]["order_id"] == "123456"


def test_portfolio_snapshots(tmp_path: Path):
    db_file = tmp_path / "test_carrotquant.db"
    db = Database(db_path=db_file)

    s1 = db.record_portfolio_snapshot(
        total_equity=100000.0,
        free_usd=100000.0,
        locked_usd=0.0,
        positions_value=0.0,
        unrealized_pnl=0.0,
        total_profit_pct=0.0,
        drawdown_pct=0.0,
    )
    s2 = db.record_portfolio_snapshot(
        total_equity=102500.0,
        free_usd=75000.0,
        locked_usd=0.0,
        positions_value=27500.0,
        unrealized_pnl=2500.0,
        total_profit_pct=2.5,
        drawdown_pct=0.0,
    )
    assert s1 is not None and s2 is not None

    history_df = db.get_portfolio_history(limit=10)
    assert len(history_df) == 2
    assert pytest.approx(history_df.iloc[1]["total_equity"]) == 102500.0
    assert pytest.approx(history_df.iloc[1]["total_profit_pct"]) == 2.5


def test_active_positions_crud(tmp_path: Path):
    db_file = tmp_path / "test_carrotquant.db"
    db = Database(db_path=db_file)

    # 1. Save new position
    db.save_active_position(
        pair="ETH/USD",
        side="BUY",
        entry_price=3500.0,
        quantity=5.0,
        collateral=0.0,
        stop_loss=3350.0,
        take_profit=3800.0,
        atr=100.0,
        highest_price=3500.0,
        lowest_price=3500.0,
    )

    positions = db.get_active_positions()
    assert len(positions) == 1
    assert positions[0]["pair"] == "ETH/USD"
    assert pytest.approx(positions[0]["stop_loss"]) == 3350.0

    # 2. Update existing position (ratchet stop loss higher)
    db.save_active_position(
        pair="ETH/USD",
        side="BUY",
        entry_price=3500.0,
        quantity=5.0,
        collateral=0.0,
        stop_loss=3480.0,
        take_profit=3800.0,
        atr=100.0,
        highest_price=3650.0,
        lowest_price=3500.0,
    )

    positions_updated = db.get_active_positions()
    assert len(positions_updated) == 1
    assert pytest.approx(positions_updated[0]["stop_loss"]) == 3480.0
    assert pytest.approx(positions_updated[0]["highest_price"]) == 3650.0

    # 3. Remove position upon exit
    db.remove_active_position("ETH/USD")
    positions_empty = db.get_active_positions()
    assert len(positions_empty) == 0
