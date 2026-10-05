"""
Risk management package for CarrotQuant.
Includes ATR volatility parity sizing, dynamic trailing stops, and portfolio circuit breaker.
"""
from risk.position_sizer import PositionSizer, OrderSizeResult
from risk.stop_loss_manager import StopLossManager, TrackedPosition, ExitSignal
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus

__all__ = [
    "PositionSizer",
    "OrderSizeResult",
    "StopLossManager",
    "TrackedPosition",
    "ExitSignal",
    "CircuitBreaker",
    "CircuitBreakerStatus",
]
