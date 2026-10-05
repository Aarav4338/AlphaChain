"""
AlphaChain E2E Test Suite - Shared Fixtures and Utilities.
Provides realistic fixtures, mock exchanges, market data synthesizers,
and test isolation for Tiers 1-4.
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import time
from typing import Dict, List, Any, Optional
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from config.trading_config import trading_config
from storage.db import Database
from strategy.regime_detector import MarketRegime

# Resilient imports supporting modular signals
try:
    from strategy.signals import TradeSignal, SignalAction
except ImportError:
    from strategy.multi_regime_alpha import TradeSignal, SignalAction


@pytest.fixture
def mock_roostoo_client():
    """
    Produces a fully authenticated, spec-compliant mock of RoostooClient.
    Tracks virtual balances, order books, and fills without network I/O.
    """
    client = MagicMock()
    client.api_key = "MOCK_KEY_E2E"
    client.secret_key = "MOCK_SECRET_E2E"
    client.base_url = "https://mock-api.roostoo.com"

    # Virtual state
    balances = {
        "USD": {"Free": 100000.0, "Locked": 0.0},
        "BTC": {"Free": 0.0, "Locked": 0.0},
        "ETH": {"Free": 0.0, "Locked": 0.0},
        "SOL": {"Free": 0.0, "Locked": 0.0},
        "DOGE": {"Free": 0.0, "Locked": 0.0},
        "AVAX": {"Free": 0.0, "Locked": 0.0},
        "SUI": {"Free": 0.0, "Locked": 0.0},
    }
    short_positions = {}
    order_counter = 5000000

    def mock_server_time():
        return int(time.time() * 1000)

    def mock_get_balance():
        return {
            "Success": True,
            "Wallet": {k: {"Free": v["Free"], "Locked": v["Locked"]} for k, v in balances.items()},
        }

    def mock_get_ticker(pair: Optional[str] = None):
        prices = {
            "BTC/USD": 65000.0,
            "ETH/USD": 2600.0,
            "SOL/USD": 150.0,
            "BNB/USD": 580.0,
            "XRP/USD": 0.55,
            "DOGE/USD": 0.12,
            "ADA/USD": 0.35,
            "AVAX/USD": 28.0,
            "LINK/USD": 11.5,
            "NEAR/USD": 4.8,
            "SUI/USD": 1.95,
            "APT/USD": 8.5,
        }
        if pair:
            p = prices.get(pair, 100.0)
            return {
                "Success": True,
                "Data": {pair: {"LastPrice": p, "BidPrice": round(p * 0.9995, 4), "AskPrice": round(p * 1.0005, 4)}},
            }
        data = {
            k: {"LastPrice": v, "BidPrice": round(v * 0.9995, 4), "AskPrice": round(v * 1.0005, 4)}
            for k, v in prices.items()
        }
        return {"Success": True, "Data": data}

    def mock_place_order(pair: str, side: str, order_type: str, quantity: float, price: Optional[float] = None):
        nonlocal order_counter
        order_counter += 1
        return {
            "Success": True,
            "OrderDetail": {
                "OrderID": order_counter,
                "Pair": pair,
                "Side": side,
                "Type": order_type,
                "Quantity": quantity,
                "Price": price or 100.0,
                "Status": "FILLED",
                "FilledQuantity": quantity,
                "AveragePrice": price or 100.0,
            },
        }

    client.get_server_time.side_effect = mock_server_time
    client.check_server_time.return_value = True
    client.get_balance.side_effect = mock_get_balance
    client.get_ticker.side_effect = mock_get_ticker
    client.place_order.side_effect = mock_place_order
    client.cancel_order.return_value = {"Success": True, "Message": "Order canceled"}
    client.get_pending_order_count.return_value = {"Success": True, "PendingCount": 0}
    client.get_short_positions.return_value = {"Success": True, "Positions": short_positions}

    return client


@pytest.fixture
def isolated_db(tmp_path: Path):
    """Initializes a genuine SQLite database instance in isolated temp storage."""
    db_file = tmp_path / "alphachain_e2e.db"
    db = Database(db_path=db_file)
    return db


@pytest.fixture
def heartbeat_workspace(tmp_path: Path):
    """Provides isolated directory structure for heartbeat and log verification."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    hb_json = logs_dir / "heartbeat.json"
    hb_log = logs_dir / "heartbeat.log"
    return {"logs_dir": logs_dir, "hb_json": hb_json, "hb_log": hb_log}


@pytest.fixture
def synthetic_candle_factory():
    """Generates realistic synthetic OHLCV candle DataFrames for backtesting and factor calculation."""
    def _create_candles(
        pair: str = "BTC/USD",
        bars: int = 120,
        base_price: float = 65000.0,
        trend: float = 0.0005,
        volatility: float = 0.008,
        seed: int = 42,
    ) -> pd.DataFrame:
        np.random.seed(seed)
        now_ts = int(time.time())
        timestamps = [now_ts - (bars - i) * 3600 for i in range(bars)]
        
        returns = np.random.normal(loc=trend, scale=volatility, size=bars)
        price_path = base_price * np.exp(np.cumsum(returns))
        
        high = price_path * (1.0 + np.abs(np.random.normal(0, volatility * 0.5, size=bars)))
        low = price_path * (1.0 - np.abs(np.random.normal(0, volatility * 0.5, size=bars)))
        opens = np.roll(price_path, 1)
        opens[0] = base_price
        closes = price_path
        volume = np.random.uniform(500.0, 5000.0, size=bars)

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": high,
            "low": low,
            "close": closes,
            "volume": volume,
        })
        return df

    return _create_candles


@pytest.fixture
def signal_factory():
    """Generates authentic TradeSignal instances for sizing and execution tests."""
    def _create_signal(
        pair: str,
        action: SignalAction = SignalAction.BUY,
        entry_price: float = 100.0,
        stop_dist_pct: float = 0.04,
        conviction: float = 0.80,
    ) -> TradeSignal:
        if action in (SignalAction.BUY, SignalAction.SHORT_CLOSE):
            stop_price = entry_price * (1.0 - stop_dist_pct)
            tp_price = entry_price * (1.0 + stop_dist_pct * 3.0)
        else:
            stop_price = entry_price * (1.0 + stop_dist_pct)
            tp_price = entry_price * (1.0 - stop_dist_pct * 3.0)

        return TradeSignal(
            pair=pair,
            action=action,
            entry_price=entry_price,
            stop_loss_price=stop_price,
            take_profit_price=tp_price,
            conviction=conviction,
            timestamp=int(time.time()),
        )

    return _create_signal
