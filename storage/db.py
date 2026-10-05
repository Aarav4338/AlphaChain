"""
Embedded SQLite Database Manager for CarrotQuant.
Maintains persistent trade audit trails, periodic portfolio equity snapshots,
and active position state for crash recovery.
"""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import pandas as pd

from config.settings import settings

logger = logging.getLogger("CarrotQuant.Database")


class Database:
    """Manages SQLite storage for trades, portfolio snapshots, and active positions."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path: Path = db_path or settings.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a connection with dictionary row access and autocommit enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Initialize database tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Trades Audit Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                timestamp TEXT NOT NULL,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,            -- BUY, SELL, SHORT_OPEN, SHORT_CLOSE
                order_type TEXT NOT NULL,      -- LIMIT, MARKET
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                collateral REAL DEFAULT 0.0,
                fee REAL DEFAULT 0.0,
                fee_coin TEXT DEFAULT 'USD',
                realized_pnl REAL DEFAULT 0.0,
                reason TEXT
            );
            """)

            # 2. Portfolio Equity Snapshots Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total_equity REAL NOT NULL,
                free_usd REAL NOT NULL,
                locked_usd REAL NOT NULL,
                positions_value REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0.0,
                total_profit_pct REAL DEFAULT 0.0,
                drawdown_pct REAL DEFAULT 0.0
            );
            """)

            # 3. Active Positions Table (for Crash Recovery)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                pair TEXT PRIMARY KEY,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                collateral REAL DEFAULT 0.0,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                atr REAL NOT NULL,
                highest_price REAL NOT NULL,
                lowest_price REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            """)

            # Indexes for fast querying
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades(pair);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON portfolio_snapshots(timestamp);")
            conn.commit()

        logger.debug(f"Initialized database schema at {self.db_path}")

    # --------------------------------------------------------------------------
    # Trades Logging
    # --------------------------------------------------------------------------

    def log_trade(
        self,
        pair: str,
        side: str,
        order_type: str,
        price: float,
        quantity: float,
        collateral: float = 0.0,
        fee: float = 0.0,
        fee_coin: str = "USD",
        realized_pnl: float = 0.0,
        order_id: Optional[str] = None,
        reason: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Log an executed trade into the audit table. Returns inserted row id."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO trades (order_id, timestamp, pair, side, order_type, price, quantity, collateral, fee, fee_coin, realized_pnl, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    str(order_id) if order_id is not None else None,
                    ts,
                    pair,
                    side,
                    order_type,
                    price,
                    quantity,
                    collateral,
                    fee,
                    fee_coin,
                    realized_pnl,
                    reason,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_all_trades(self, limit: int = 500) -> pd.DataFrame:
        """Fetch historical trades as a Pandas DataFrame."""
        with self._get_connection() as conn:
            query = "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?;"
            df = pd.read_sql_query(query, conn, params=(limit,))
            return df

    # --------------------------------------------------------------------------
    # Portfolio Snapshots
    # --------------------------------------------------------------------------

    def record_portfolio_snapshot(
        self,
        total_equity: float,
        free_usd: float,
        locked_usd: float,
        positions_value: float,
        unrealized_pnl: float = 0.0,
        total_profit_pct: float = 0.0,
        drawdown_pct: float = 0.0,
        timestamp: Optional[str] = None,
    ) -> int:
        """Record an hourly portfolio equity snapshot."""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO portfolio_snapshots (timestamp, total_equity, free_usd, locked_usd, positions_value, unrealized_pnl, total_profit_pct, drawdown_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    ts,
                    total_equity,
                    free_usd,
                    locked_usd,
                    positions_value,
                    unrealized_pnl,
                    total_profit_pct,
                    drawdown_pct,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_portfolio_history(self, limit: int = 1000) -> pd.DataFrame:
        """Fetch portfolio equity snapshots as a Pandas DataFrame."""
        with self._get_connection() as conn:
            query = "SELECT * FROM portfolio_snapshots ORDER BY timestamp ASC LIMIT ?;"
            df = pd.read_sql_query(query, conn, params=(limit,))
            return df

    # --------------------------------------------------------------------------
    # Active Positions (Crash Recovery & State Sync)
    # --------------------------------------------------------------------------

    def save_active_position(
        self,
        pair: str,
        side: str,
        entry_price: float,
        quantity: float,
        collateral: float,
        stop_loss: float,
        take_profit: float,
        atr: float,
        highest_price: float,
        lowest_price: float,
        created_at: Optional[str] = None,
    ) -> None:
        """Save or update active position state for crash persistence."""
        ts = created_at or datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO active_positions (pair, side, entry_price, quantity, collateral, stop_loss, take_profit, atr, highest_price, lowest_price, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pair) DO UPDATE SET
            stop_loss = excluded.stop_loss,
            take_profit = excluded.take_profit,
            highest_price = excluded.highest_price,
            lowest_price = excluded.lowest_price;
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query,
                (
                    pair,
                    side,
                    entry_price,
                    quantity,
                    collateral,
                    stop_loss,
                    take_profit,
                    atr,
                    highest_price,
                    lowest_price,
                    ts,
                ),
            )
            conn.commit()

    def remove_active_position(self, pair: str) -> None:
        """Remove a closed position from active positions table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_positions WHERE pair = ?;", (pair,))
            conn.commit()

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """Retrieve all active positions saved in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_positions;")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
