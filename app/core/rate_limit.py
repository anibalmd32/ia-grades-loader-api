"""Límite de uso por clave (ventana deslizante en memoria).

Se guarda en la memoria del proceso: con varias instancias de la API cada una lleva su
propia cuenta. Es suficiente para frenar abusos y el gasto en la IA; si algún día hay muchas
instancias, se puede mover a la base de datos o a Redis sin cambiar a quien lo usa.
"""

import math
import time
from collections import defaultdict, deque
from collections.abc import Callable, Hashable

from app.core.errors import AppError


class RateLimitedError(AppError):
    status_code = 429

    def __init__(self, retry_after: int):
        super().__init__(
            f"Has hecho muchas preguntas seguidas. Espera {retry_after} s e inténtalo de nuevo."
        )
        self.retry_after = retry_after

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


class RateLimiter:
    def __init__(
        self,
        limit: Callable[[], int],
        window_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        # `limit` es una función para leer siempre la configuración vigente (0 = sin límite)
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: dict[Hashable, deque[float]] = defaultdict(deque)

    def hit(self, key: Hashable) -> None:
        """Registra un uso; si se supera el límite lanza RateLimitedError y no lo cuenta."""
        limit = self._limit()
        if limit <= 0:
            return
        now = self._clock()
        events = self._events[key]
        while events and events[0] <= now - self._window:
            events.popleft()
        if len(events) >= limit:
            raise RateLimitedError(retry_after=max(1, math.ceil(events[0] + self._window - now)))
        events.append(now)

    def reset(self) -> None:
        self._events.clear()
