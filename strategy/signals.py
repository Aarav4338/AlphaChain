from dataclasses import dataclass
from enum import Enum


class SignalAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT_OPEN = "SHORT_OPEN"
    SHORT_CLOSE = "SHORT_CLOSE"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    pair: str
    action: SignalAction
    conviction: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    atr: float = 0.0
    reason: str = ""
    timestamp: int = 0
