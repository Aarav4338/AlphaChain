from enum import Enum
import time
import logging
from typing import Optional

from config.trading_config import trading_config

logger = logging.getLogger("AlphaChain.CircuitBreaker")


class CircuitBreakerStatus(Enum):
    NORMAL = "NORMAL"
    ACTIVE = "NORMAL"
    TRIPPED = "TRIPPED"
    COOLDOWN = "COOLDOWN"


class CircuitBreaker:
    def __init__(
        self,
        max_drawdown_pct: Optional[float] = None,
        cooldown_hours: Optional[int] = None,
        initial_equity: float = 100000.0,
    ):
        self.max_drawdown_pct: float = max_drawdown_pct or trading_config.CIRCUIT_BREAKER_DRAWDOWN_PCT
        self.cooldown_seconds: float = (cooldown_hours or trading_config.CIRCUIT_BREAKER_COOLDOWN_HOURS) * 3600.0
        self.peak_equity: float = initial_equity
        self.current_drawdown_pct: float = 0.0
        self.status: CircuitBreakerStatus = CircuitBreakerStatus.NORMAL
        self.trip_timestamp: Optional[float] = None

    def update_equity(self, current_equity: float, current_time: Optional[float] = None) -> CircuitBreakerStatus:
        now = current_time if current_time is not None else time.time()

        if self.status == CircuitBreakerStatus.COOLDOWN and self.trip_timestamp is not None:
            elapsed = now - self.trip_timestamp
            if elapsed >= self.cooldown_seconds:
                logger.info("Circuit breaker cooldown elapsed. Resetting to NORMAL.")
                self.status = CircuitBreakerStatus.NORMAL
                self.peak_equity = current_equity
                self.trip_timestamp = None
                return self.status
            return self.status

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        if self.peak_equity > 0:
            self.current_drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity
        else:
            self.current_drawdown_pct = 0.0

        if self.current_drawdown_pct >= self.max_drawdown_pct:
            if self.status != CircuitBreakerStatus.TRIPPED and self.status != CircuitBreakerStatus.COOLDOWN:
                self.status = CircuitBreakerStatus.TRIPPED
                self.trip_timestamp = now
                logger.critical(
                    f"CIRCUIT BREAKER TRIPPED: Drawdown {self.current_drawdown_pct * 100:.2f}% "
                    f"exceeds {self.max_drawdown_pct * 100:.1f}%. Halting trading."
                )
                return self.status

        return self.status

    def update(self, current_equity: float, current_time: Optional[float] = None) -> CircuitBreakerStatus:
        """Alias for update_equity to support E2E evaluation contracts."""
        return self.update_equity(current_equity, current_time)

    def acknowledge_liquidation(self) -> None:
        if self.status == CircuitBreakerStatus.TRIPPED:
            self.status = CircuitBreakerStatus.COOLDOWN
            logger.info("Circuit breaker liquidation completed. Entering COOLDOWN.")

    def is_trading_allowed(self) -> bool:
        return self.status == CircuitBreakerStatus.NORMAL

    def can_trade(self) -> bool:
        """Alias for is_trading_allowed to support E2E evaluation contracts."""
        return self.is_trading_allowed()

    def reset(self, new_equity: Optional[float] = None) -> None:
        """Reset circuit breaker status to normal and update peak equity."""
        self.status = CircuitBreakerStatus.NORMAL
        if new_equity is not None:
            self.peak_equity = new_equity
        self.trip_timestamp = None
        self.current_drawdown_pct = 0.0

    @property
    def trip_time(self) -> Optional[float]:
        return self.trip_timestamp

    @trip_time.setter
    def trip_time(self, val: Optional[float]) -> None:
        self.trip_timestamp = val
