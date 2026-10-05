from dataclasses import dataclass, field
from typing import List


@dataclass
class TradingConfig:
    ASSET_UNIVERSE: List[str] = field(
        default_factory=lambda: [
            "BTC/USD",
            "ETH/USD",
            "SOL/USD",
            "BNB/USD",
            "XRP/USD",
            "DOGE/USD",
            "ADA/USD",
            "AVAX/USD",
            "LINK/USD",
            "NEAR/USD",
            "SUI/USD",
            "APT/USD",
        ]
    )

    REGIME_BENCHMARK: str = "BTC/USD"
    REGIME_SECONDARY_BENCHMARK: str = "ETH/USD"

    TIMEFRAME_TREND: str = "1h"
    TIMEFRAME_ENTRY: str = "15m"
    WARMUP_CANDLES_COUNT: int = 150

    EMA_FAST: int = 20
    EMA_SLOW: int = 50
    EMA_BASELINE: int = 200
    RSI_PERIOD: int = 14
    ATR_PERIOD: int = 14
    ADX_PERIOD: int = 14
    ADX_TREND_THRESHOLD: float = 23.0
    BOLLINGER_WINDOW: int = 20
    BOLLINGER_STD_DEV: float = 2.0

    INITIAL_PORTFOLIO_USD: float = 100000.0
    DYNAMIC_CONCURRENCY_ENABLED: bool = True
    MAX_PORTFOLIO_HEAT_PCT: float = 0.010  # 1.0% cumulative risk heat ceiling
    MAX_RISK_PER_ASSET_PCT: float = 0.0050  # 0.50% max risk on any single position
    CHOP_RISK_HEAT_CEILING: float = 0.0040  # 0.40% risk ceiling in chop
    MAX_CONCURRENT_POSITIONS: int = 3  # Baseline / fallback when dynamic concurrency disabled
    MAX_RISK_PER_TRADE_PCT: float = 0.0025  # Legacy fallback risk per trade
    MAX_ALLOCATION_PER_COIN_PCT: float = 0.20
    MIN_CASH_BUFFER_PCT: float = 0.25

    STOP_LOSS_ATR_MULT: float = 1.5
    TAKE_PROFIT_ATR_MULT: float = 4.0
    BREAKEVEN_ACTIVATION_ATR_MULT: float = 1.2
    TRAILING_ACTIVATION_ATR_MULT: float = 2.0
    TRAILING_DISTANCE_ATR_MULT: float = 1.8
    MAX_HOLDING_BARS: int = 0

    CIRCUIT_BREAKER_DRAWDOWN_PCT: float = 0.04
    CIRCUIT_BREAKER_COOLDOWN_HOURS: int = 12

    TAKER_FEE_RATE: float = 0.0010
    MAKER_FEE_RATE: float = 0.0005
    MAKER_ORDER_TIMEOUT_SECONDS: int = 60
    MAX_API_CALLS_PER_MINUTE: int = 22

    MAIN_LOOP_SLEEP_SECONDS: int = 30
    EQUITY_SNAPSHOT_INTERVAL_SECONDS: int = 3600


trading_config = TradingConfig()
