import time
import threading
from typing import Optional


class RateLimiter:
    def __init__(self, max_calls: int = 22, period_seconds: float = 60.0):
        self.max_calls: int = max_calls
        self.period: float = period_seconds
        self.tokens: float = float(max_calls)
        self.last_update: float = time.time()
        self.lock: threading.Lock = threading.Lock()

    def acquire(self, tokens_needed: float = 1.0, timeout: Optional[float] = 30.0) -> bool:
        start_time = time.time()

        while True:
            with self.lock:
                now = time.time()
                elapsed = now - self.last_update
                self.last_update = now

                self.tokens = min(
                    float(self.max_calls),
                    self.tokens + elapsed * (self.max_calls / self.period),
                )

                if self.tokens >= tokens_needed:
                    self.tokens -= tokens_needed
                    return True

                missing_tokens = tokens_needed - self.tokens
                sleep_needed = missing_tokens * (self.period / self.max_calls)

            if timeout is not None and (time.time() - start_time) + sleep_needed > timeout:
                return False

            time.sleep(max(0.05, min(sleep_needed, 1.0)))

    def get_available_tokens(self) -> float:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            return min(
                float(self.max_calls),
                self.tokens + elapsed * (self.max_calls / self.period),
            )
