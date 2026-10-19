"""Avisos en tiempo real: canal en memoria entre quien guarda notas y los estudiantes
conectados a GET /student/notifications/stream (Server-Sent Events).

Vale para un solo proceso de la API (como el despliegue actual). Con varias instancias, un
aviso solo llegaría al estudiante conectado a la misma: haría falta un canal compartido
(p. ej. Redis pub/sub). Aun así no se pierde nada: los avisos están en la base de datos y la
app los consulta cada cierto tiempo como respaldo.
"""

import asyncio
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from app.models import Notification
from app.schemas.notifications import NotificationRead

Payload = dict[str, Any]

# Avisos pendientes de enviar por conexión; si un cliente no lee, se descartan (los tiene
# igualmente en la base de datos)
QUEUE_SIZE = 100


class NotificationBroker:
    def __init__(self) -> None:
        self._queues: dict[int, set[asyncio.Queue[Payload]]] = defaultdict(set)

    @contextmanager
    def subscribe(self, user_id: int) -> Iterator[asyncio.Queue[Payload]]:
        """Cola con los avisos nuevos del usuario (ya en JSON) mientras dure la conexión."""
        queue: asyncio.Queue[Payload] = asyncio.Queue(QUEUE_SIZE)
        self._queues[user_id].add(queue)
        try:
            yield queue
        finally:
            self._queues[user_id].discard(queue)
            if not self._queues[user_id]:
                del self._queues[user_id]

    def publish(self, notifications: list[Notification]) -> None:
        """Avisa a las conexiones de cada destinatario (llamar después del COMMIT)."""
        for notification in notifications:
            queues = self._queues.get(notification.user_id)
            if not queues:
                continue
            payload = NotificationRead.model_validate(notification).model_dump(mode="json")
            for queue in queues:
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(payload)

    def connections(self, user_id: int) -> int:
        return len(self._queues.get(user_id, ()))


broker = NotificationBroker()
