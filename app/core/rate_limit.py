"""Límite de uso por clave (ventana deslizante en memoria).

Se guarda en la memoria del proceso: con varias instancias de la API cada una lleva su
propia cuenta. Es suficiente para frenar abusos y el gasto en la IA; si algún día hay muchas
instancias, se puede mover a la base de datos o a Redis sin cambiar a quien lo usa.
"""

import math
import time
from collections import deque
from collections.abc import Callable, Hashable

from app.core.errors import AppError

CHAT_LIMIT_MESSAGE = "Has hecho muchas preguntas seguidas. Espera {seconds} s e inténtalo de nuevo."

# Cada cuántos usos se borran las claves sin actividad reciente: sin esto, un atacante que
# prueba miles de emails o IPs distintas haría crecer la memoria sin límite.
_SWEEP_EVERY = 1000


class RateLimitedError(AppError):
    status_code = 429

    def __init__(self, retry_after: int, message: str = CHAT_LIMIT_MESSAGE):
        super().__init__(message.format(seconds=retry_after))
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
        message: str = CHAT_LIMIT_MESSAGE,
    ):
        # `limit` es una función para leer siempre la configuración vigente (0 = sin límite)
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._message = message
        self._events: dict[Hashable, deque[float]] = {}
        self._hits_since_sweep = 0

    def _recent(self, key: Hashable, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - self._window:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def check(self, key: Hashable) -> None:
        """Lanza RateLimitedError si la clave ya agotó su límite, sin contar un uso."""
        limit = self._limit()
        if limit <= 0:
            return
        now = self._clock()
        events = self._recent(key, now)
        if len(events) >= limit:
            retry_after = max(1, math.ceil(events[0] + self._window - now))
            raise RateLimitedError(retry_after=retry_after, message=self._message)

    def add(self, key: Hashable) -> None:
        """Cuenta un uso sin comprobar el límite (p. ej. un intento de login fallido)."""
        if self._limit() <= 0:
            return
        now = self._clock()
        self._events.setdefault(key, deque()).append(now)
        self._hits_since_sweep += 1
        if self._hits_since_sweep >= _SWEEP_EVERY:
            self._sweep(now)

    def hit(self, key: Hashable) -> None:
        """Registra un uso; si se supera el límite lanza RateLimitedError y no lo cuenta."""
        self.check(key)
        self.add(key)

    def clear(self, key: Hashable) -> None:
        """Olvida los usos de una clave (p. ej. tras un login correcto)."""
        self._events.pop(key, None)

    def _sweep(self, now: float) -> None:
        self._hits_since_sweep = 0
        for key in list(self._events):
            self._recent(key, now)

    def reset(self) -> None:
        self._events.clear()
        self._hits_since_sweep = 0
