from dataclasses import dataclass, field
from typing import List


@dataclass
class TradingConfig:
    ASSET_UNIVERSE_12: List[str] = field(
        default_factory=lambda: [
            "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "DOGE/USD",
            "ADA/USD", "AVAX/USD", "LINK/USD", "NEAR/USD", "SUI/USD", "APT/USD",
        ]
    )

    ASSET_UNIVERSE_44: List[str] = field(
        default_factory=lambda: [
            # 1. Mega-Cap Benchmarks & Majors (12)
            "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "DOGE/USD",
            "ADA/USD", "AVAX/USD", "LINK/USD", "NEAR/USD", "SUI/USD", "APT/USD",

            # 2. Top Liquid US Tech & Crypto-Proxy Stocks (9)
            "NVDAB/USD",  # Nvidia (AI sector momentum)
            "TSLAB/USD",  # Tesla (High retail volatility)
            "MSTRB/USD",  # MicroStrategy (BTC treasury proxy)
            "COINB/USD",  # Coinbase (Crypto exchange proxy)
            "PLTRB/USD",  # Palantir (Enterprise AI)
            "AMDB/USD",   # AMD (Semiconductors)
            "GOOGLB/USD", # Google (Tech benchmark)
            "METAB/USD",  # Meta (Social & AI)
            "MSFTB/USD",  # Microsoft (Tech benchmark)

            # 3. High-Momentum DeFi, L1/L2 & AI Altcoins (19)
            "AAVE/USD", "ARB/USD", "CRV/USD", "DOT/USD", "ENA/USD",
            "FET/USD", "FIL/USD", "ICP/USD", "LTC/USD", "ONDO/USD", "PENDLE/USD",
            "POL/USD", "SEI/USD", "TAO/USD", "HBAR/USD",
            "TRX/USD", "UNI/USD", "WLD/USD", "XLM/USD",
        ]
    )

    ASSET_UNIVERSE: List[str] = field(
        default_factory=lambda: [
            # 1. Mega-Cap Benchmarks & Majors (12)
            "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "DOGE/USD",
            "ADA/USD", "AVAX/USD", "LINK/USD", "NEAR/USD", "SUI/USD", "APT/USD",

            # 2. Top Liquid US Tech & Crypto-Proxy Stocks (9)
            "NVDAB/USD", "TSLAB/USD", "MSTRB/USD", "COINB/USD", "PLTRB/USD",
            "AMDB/USD", "GOOGLB/USD", "METAB/USD", "MSFTB/USD",

            # 3. High-Momentum DeFi, L1/L2 & AI Altcoins (19)
            "AAVE/USD", "ARB/USD", "CRV/USD", "DOT/USD", "ENA/USD",
            "FET/USD", "FIL/USD", "ICP/USD", "LTC/USD", "ONDO/USD", "PENDLE/USD",
            "POL/USD", "SEI/USD", "TAO/USD", "HBAR/USD",
            "TRX/USD", "UNI/USD", "WLD/USD", "XLM/USD",
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
    MAX_RISK_PER_ASSET_PCT: float = 0.0050
    CHOP_RISK_HEAT_CEILING: float = 0.0040
    BEAR_RISK_HEAT_CEILING: float = 0.0120
    MAX_CONCURRENT_POSITIONS: int = 8
    MAX_RISK_PER_TRADE_PCT: float = 0.0025  # Legacy fallback risk per trade
    MAX_ALLOCATION_PER_COIN_PCT: float = 0.20
    MIN_CASH_BUFFER_PCT: float = 0.25
    MIN_POSITION_CAPITAL_USD: float = 10.0

    STOP_LOSS_ATR_MULT: float = 1.5
    TAKE_PROFIT_ATR_MULT: float = 1.8
    BREAKEVEN_ACTIVATION_ATR_MULT: float = 1.2
    TRAILING_ACTIVATION_ATR_MULT: float = 2.0
    TRAILING_DISTANCE_ATR_MULT: float = 1.8
    MAX_HOLDING_BARS: int = 0

    # Trend Pyramiding / Scale-In on Winners
    PYRAMIDING_ENABLED: bool = False
    PYRAMIDING_PROFIT_ATR_MULT: float = 1.5
    PYRAMIDING_TRANCHE_SCALE: float = 0.50
    MAX_PYRAMID_TRANCHES: int = 1

    CIRCUIT_BREAKER_DRAWDOWN_PCT: float = 0.04
    CIRCUIT_BREAKER_COOLDOWN_HOURS: int = 12

    TAKER_FEE_RATE: float = 0.0010
    MAKER_FEE_RATE: float = 0.0005
    MAKER_ORDER_TIMEOUT_SECONDS: int = 60
    MAX_API_CALLS_PER_MINUTE: int = 22

    MAIN_LOOP_SLEEP_SECONDS: int = 30
    EQUITY_SNAPSHOT_INTERVAL_SECONDS: int = 3600

    def apply_profile(self, profile: str = "active_dynamic") -> None:
        """Apply pre-calibrated parameter profiles for dynamic active vs baseline sniper trading."""
        if profile in ("concentrated_alpha", "calibrated_optimal", "active_dynamic"):
            self.ASSET_UNIVERSE = self.ASSET_UNIVERSE_44.copy()
            self.ADX_TREND_THRESHOLD = 20.0
            self.DYNAMIC_CONCURRENCY_ENABLED = False
            self.MAX_CONCURRENT_POSITIONS = 8        # Breathing room: allows 6-8 concurrent positions ($10k-$20k each)
            self.MAX_PORTFOLIO_HEAT_PCT = 0.0200     # 2.0% cumulative risk heat ceiling
            self.MAX_RISK_PER_ASSET_PCT = 0.0060     # 0.6% ($600) risk per asset
            self.CHOP_RISK_HEAT_CEILING = 0.0180     # 1.8% in CHOP regime (ample heat for 3-4 full positions)
            self.BEAR_RISK_HEAT_CEILING = 0.0180
            self.MAX_ALLOCATION_PER_COIN_PCT = 0.20  # Up to $20,000 per coin on top conviction
            self.MIN_CASH_BUFFER_PCT = 0.20          # 80% capital deployment with 20% safe cash floor
            self.MIN_POSITION_CAPITAL_USD = 8000.0   # Enforce $8,000 minimum trade capital floor (no micro-dust)
            self.STOP_LOSS_ATR_MULT = 1.50
            self.TAKE_PROFIT_ATR_MULT = 2.20         # Multi-ATR profit target
            self.BREAKEVEN_ACTIVATION_ATR_MULT = 1.20
            self.TRAILING_ACTIVATION_ATR_MULT = 1.80
            self.TRAILING_DISTANCE_ATR_MULT = 1.50
            self.MAX_HOLDING_BARS = 0               # No arbitrary time barrier; let runners run!
            self.PYRAMIDING_ENABLED = False         # Full size entered upfront into top conviction trades
        elif profile == "pyramiding_optimal":
            self.ASSET_UNIVERSE = self.ASSET_UNIVERSE_44.copy()
            self.ADX_TREND_THRESHOLD = 23.11
            self.MAX_CONCURRENT_POSITIONS = 8
            self.MAX_PORTFOLIO_HEAT_PCT = 0.0200
            self.MAX_RISK_PER_ASSET_PCT = 0.0075
            self.CHOP_RISK_HEAT_CEILING = 0.0090
            self.BEAR_RISK_HEAT_CEILING = 0.0150
            self.MAX_ALLOCATION_PER_COIN_PCT = 0.30  # Allows capital room for winning pyramid additions
            self.MIN_CASH_BUFFER_PCT = 0.15          # Deploy up to 85% of capital (15% cash floor)
            self.STOP_LOSS_ATR_MULT = 1.50
            self.TAKE_PROFIT_ATR_MULT = 1.80         # Fast, reachable profit target
            self.BREAKEVEN_ACTIVATION_ATR_MULT = 0.80 # Lock breakeven early at +0.8x ATR
            self.TRAILING_ACTIVATION_ATR_MULT = 1.20
            self.TRAILING_DISTANCE_ATR_MULT = 1.00
            self.MAX_HOLDING_BARS = 32               # Extended horizon for trending winners
            self.PYRAMIDING_ENABLED = True
            self.PYRAMIDING_PROFIT_ATR_MULT = 1.5
            self.PYRAMIDING_TRANCHE_SCALE = 0.50
            self.MAX_PYRAMID_TRANCHES = 1
        elif profile == "baseline_sniper":
            self.ASSET_UNIVERSE = self.ASSET_UNIVERSE_12.copy()
            self.ADX_TREND_THRESHOLD = 23.0
            self.MAX_PORTFOLIO_HEAT_PCT = 0.010
            self.MAX_RISK_PER_ASSET_PCT = 0.0050
            self.CHOP_RISK_HEAT_CEILING = 0.0040
            self.MAX_ALLOCATION_PER_COIN_PCT = 0.20
            self.MIN_CASH_BUFFER_PCT = 0.25
            self.STOP_LOSS_ATR_MULT = 1.5
            self.TAKE_PROFIT_ATR_MULT = 4.0
            self.BREAKEVEN_ACTIVATION_ATR_MULT = 1.2
            self.MAX_HOLDING_BARS = 0
            self.PYRAMIDING_ENABLED = False

    def reset_defaults(self) -> None:
        """Reset all parameters to default baseline configuration."""
        self.apply_profile("baseline_sniper")
        self.ASSET_UNIVERSE = self.ASSET_UNIVERSE_12.copy()
        self.DYNAMIC_CONCURRENCY_ENABLED = True
        self.TRAILING_ACTIVATION_ATR_MULT = 2.0
        self.TRAILING_DISTANCE_ATR_MULT = 1.8
        self.MAX_CONCURRENT_POSITIONS = 3
        self.PYRAMIDING_PROFIT_ATR_MULT = 1.5
        self.PYRAMIDING_TRANCHE_SCALE = 0.50
        self.MAX_PYRAMID_TRANCHES = 1


trading_config = TradingConfig()
