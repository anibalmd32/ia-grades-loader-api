"""Worker de la cola de importaciones.

Corre dentro de la API (IMPORT_WORKER_EMBEDDED=true) o como proceso aparte:

    uv run python -m app.worker

Se pueden lanzar varios procesos: cada trabajo lo toma un solo worker.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.client import LLMClient, get_llm
from app.core.config import get_settings
from app.services import import_jobs

logger = logging.getLogger(__name__)

# Worker del proceso actual (si lo hay), para despertarlo al encolar
_current: "ImportWorker | None" = None


def notify_new_job() -> None:
    """Despierta al worker embebido para que no espere al siguiente sondeo."""
    if _current is not None:
        _current.wake.set()


class ImportWorker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm_factory: Callable[[], LLMClient] = get_llm,
        *,
        concurrency: int | None = None,
        poll_seconds: float | None = None,
    ):
        settings = get_settings()
        self.sessionmaker = sessionmaker
        self.llm_factory = llm_factory
        self.concurrency = concurrency or settings.import_worker_concurrency
        self.poll_seconds = poll_seconds or settings.import_worker_poll_seconds
        self.wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        global _current
        _current = self
        async with self.sessionmaker() as db:
            if recovered := await import_jobs.recover_stale(db):
                logger.warning("Reencoladas %s importaciones interrumpidas", recovered)
        self._tasks = [
            asyncio.create_task(self._loop(n), name=f"import-worker-{n}")
            for n in range(self.concurrency)
        ]
        logger.info("Worker de importaciones iniciado (%s en paralelo)", self.concurrency)

    async def stop(self) -> None:
        global _current
        self._stopping.set()
        self.wake.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if _current is self:
            _current = None

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stopping.wait()
        finally:
            await self.stop()

    async def _loop(self, n: int) -> None:
        while not self._stopping.is_set():
            try:
                async with self.sessionmaker() as db:
                    job_id = await import_jobs.claim_next(db)
                if job_id is None:
                    await self._idle()
                    continue
                logger.info("Worker %s procesando la importación %s", n, job_id)
                await import_jobs.process_job(self.sessionmaker, self.llm_factory(), job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # p. ej. la BD caída: se reintenta tras una pausa en vez de matar el worker
                logger.exception("Error en el worker de importaciones")
                await self._idle()

    async def _idle(self) -> None:
        self.wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.wake.wait(), timeout=self.poll_seconds)


def main() -> None:
    from app.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = ImportWorker(SessionLocal)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(worker.run_forever())


if __name__ == "__main__":
    main()
