import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import ImportJob, JobStatus, Role
from app.models.import_job import utcnow
from app.services.import_jobs import claim_next, recover_stale
from app.worker import ImportWorker
from tests.import_helpers import FakeLLM, entry, upload


async def wait_until(predicate, timeout: float = 5.0) -> None:
    async def _loop():
        while not await predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_loop(), timeout)


async def all_finished(sessionmaker) -> bool:
    async with sessionmaker() as db:
        statuses = set(await db.scalars(select(ImportJob.status)))
    return bool(statuses) and statuses <= {JobStatus.DONE, JobStatus.FAILED}


@pytest.mark.skip(
    reason="PENDIENTE: se cuelga de forma intermitente en el 3er trabajo (también con un solo "
    "worker). Sin diagnosticar si es un fallo real o del montaje de tests (SQLite en memoria). "
    "Ver tarea en el tablero de Notion."
)
async def test_worker_procesa_la_cola_en_paralelo_sin_duplicar(client, course, sessionmaker):
    fake = FakeLLM({"grades": [entry(cedula="V-1", term=1, value=15)]})
    job_ids = []
    for _ in range(3):
        response = await upload(client, course)
        assert response.status_code == 202
        job_ids.append(response.json()["id"])

    worker = ImportWorker(sessionmaker, lambda: fake, concurrency=2, poll_seconds=0.05)
    await worker.start()
    try:
        await wait_until(lambda: all_finished(sessionmaker))
    finally:
        await worker.stop()

    assert len(fake.calls) == 3  # cada trabajo se procesó una sola vez
    for job_id in job_ids:
        job = (await client.get(f"/teacher/import-jobs/{job_id}", headers=course["headers"])).json()
        assert job["status"] == "done"
        assert job["result"]["summary"] == {"ok": 1, "warning": 0, "error": 0}
        assert job["finished_at"].endswith("Z") or job["finished_at"].endswith("+00:00")

    async with sessionmaker() as db:
        contents = list(await db.scalars(select(ImportJob.file_content)))
    assert contents == [None, None, None]  # el archivo se borra al terminar


async def test_un_trabajo_solo_se_reclama_una_vez(client, course, sessionmaker):
    first = (await upload(client, course)).json()["id"]
    second = (await upload(client, course)).json()["id"]
    async with sessionmaker() as db:
        assert await claim_next(db) == first
        assert await claim_next(db) == second
        assert await claim_next(db) is None
        job = await db.get(ImportJob, first)
        assert job.status == JobStatus.PROCESSING
        assert job.attempts == 1


async def test_recupera_trabajos_huerfanos(client, course, sessionmaker):
    settings = get_settings()
    old = utcnow() - timedelta(minutes=settings.import_job_stale_minutes + 1)
    retry = (await upload(client, course)).json()["id"]
    give_up = (await upload(client, course)).json()["id"]
    recent = (await upload(client, course)).json()["id"]
    async with sessionmaker() as db:
        for job_id, started, attempts in [
            (retry, old, 1),
            (give_up, old, settings.import_job_max_attempts),
            (recent, utcnow(), 1),
        ]:
            job = await db.get(ImportJob, job_id)
            job.status, job.started_at, job.attempts = JobStatus.PROCESSING, started, attempts
        await db.commit()

        assert await recover_stale(db) == 1
        db.expire_all()
        assert (await db.get(ImportJob, retry)).status == JobStatus.PENDING
        assert (await db.get(ImportJob, give_up)).status == JobStatus.FAILED
        assert (await db.get(ImportJob, recent)).status == JobStatus.PROCESSING


async def test_limite_de_importaciones_en_curso(client, course, monkeypatch):
    monkeypatch.setattr(get_settings(), "import_max_pending_per_teacher", 2)
    assert (await upload(client, course)).status_code == 202
    assert (await upload(client, course)).status_code == 202
    third = await upload(client, course)
    assert third.status_code == 429
    assert "máximo 2" in third.json()["detail"]


async def test_listado_y_privacidad(client, course, login_as):
    job_id = (await upload(client, course)).json()["id"]

    listed = (await client.get("/teacher/import-jobs", headers=course["headers"])).json()
    assert [j["id"] for j in listed] == [job_id]
    assert listed[0]["status"] == "pending"
    assert "result" not in listed[0]

    by_subject = await client.get(
        f"/teacher/import-jobs?subject_id={course['subject_id'] + 1}", headers=course["headers"]
    )
    assert by_subject.json() == []

    other = await login_as(Role.TEACHER, email="otro@uni.edu")
    assert (await client.get(f"/teacher/import-jobs/{job_id}", headers=other)).status_code == 404
    assert (await client.get("/teacher/import-jobs", headers=other)).json() == []
