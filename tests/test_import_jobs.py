import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.db.base import Base
from app.models import (
    Enrollment,
    ImportJob,
    JobStatus,
    Nationality,
    Role,
    Section,
    Semester,
    StudentSection,
    Subject,
    TeacherSubject,
    User,
)
from app.models.import_job import utcnow
from app.services.import_jobs import claim_next, enqueue, mark_confirmed, recover_stale
from app.worker import ImportWorker
from tests.factories import names
from tests.import_helpers import SAMPLE, FakeLLM, entry, upload


async def wait_until(predicate, timeout: float = 5.0) -> None:
    async def _loop():
        while not await predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_loop(), timeout)


async def all_finished(sessionmaker) -> bool:
    async with sessionmaker() as db:
        statuses = set(await db.scalars(select(ImportJob.status)))
    return bool(statuses) and statuses <= {JobStatus.DONE, JobStatus.FAILED}


@pytest.fixture
async def file_sessionmaker(tmp_path):
    """SQLite en un archivo con una conexión por sesión, como MySQL en producción.

    La base en memoria del resto de tests comparte UNA conexión entre todas las sesiones:
    con dos workers a la vez, el rollback de una sesión deshacía el UPDATE pendiente de la
    otra y el trabajo quedaba en 'processing'. Era un artefacto de los tests, no del worker.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cola.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed_course(sessionmaker) -> tuple[int, int, int]:
    """Profesor con MAT-101 en la sección A y la estudiante V-1 de esa sección matriculada.
    Devuelve (profesor, materia, sección)."""
    async with sessionmaker() as db:
        semester = Semester(name="2026-I")
        section = Section(semester=semester, name="A")
        subject = Subject(code="MAT-101", name="Matemática I", semester=semester)
        teacher = User(role=Role.TEACHER, **names("Pedro"), email="p@uni.edu", password_hash="x")
        ana = User(
            role=Role.STUDENT,
            **names("Ana"),
            nationality=Nationality.VENEZOLANO,
            cedula=1,
            password_hash="x",
        )
        db.add_all([section, subject, teacher, ana])
        await db.flush()
        db.add_all(
            [
                TeacherSubject(teacher_id=teacher.id, subject_id=subject.id, section_id=section.id),
                StudentSection(student_id=ana.id, semester_id=semester.id, section_id=section.id),
                Enrollment(student_id=ana.id, subject_id=subject.id),
            ]
        )
        await db.commit()
        return teacher.id, subject.id, section.id


async def test_worker_procesa_la_cola_en_paralelo_sin_duplicar(file_sessionmaker):
    teacher_id, subject_id, section_id = await seed_course(file_sessionmaker)
    fake = FakeLLM({"grades": [entry(cedula="V-1", term=1, value=15)]})
    job_ids = []
    async with file_sessionmaker() as db:
        for n in range(4):
            job = await enqueue(
                db,
                teacher_id=teacher_id,
                subject_id=subject_id,
                section_id=section_id,
                file_name=f"notas-{n}.xlsx",
                content=SAMPLE,
                default_term=None,
            )
            job_ids.append(job.id)

    worker = ImportWorker(file_sessionmaker, lambda: fake, concurrency=2, poll_seconds=0.05)
    await worker.start()
    try:
        await wait_until(lambda: all_finished(file_sessionmaker), timeout=20)
    finally:
        await worker.stop()

    assert len(fake.calls) == 4  # cada trabajo se procesó una sola vez
    async with file_sessionmaker() as db:
        jobs = list(await db.scalars(select(ImportJob)))
    assert sorted(j.id for j in jobs) == sorted(job_ids)
    for job in jobs:
        assert job.status == JobStatus.DONE, job.error
        assert job.attempts == 1
        assert job.result["summary"] == {"ok": 1, "warning": 0, "error": 0}
        assert job.file_content is None  # el archivo se borra al terminar


async def test_un_trabajo_solo_se_reclama_una_vez(client, course, sessionmaker):
    first = (await upload(client, course)).json()["id"]
    second = (await upload(client, course)).json()["id"]
    async with sessionmaker() as db:
        # MySQL guarda DATETIME al segundo: se separan para que el orden FIFO no sea un empate
        (await db.get(ImportJob, first)).created_at = utcnow() - timedelta(seconds=5)
        await db.commit()
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


async def test_resumen_incluye_intentos_y_confirmacion(client, course, sessionmaker):
    job_id = (await upload(client, course)).json()["id"]
    url = f"{course['url']}/grades"
    ana = course["students"]["Ana Pérez"]
    body = {"grades": [{"student_id": ana, "term": 1, "value": 15}], "import_job_id": job_id}

    listed = (await client.get("/teacher/import-jobs", headers=course["headers"])).json()
    assert listed[0]["attempts"] == 0
    assert listed[0]["confirmed_at"] is None

    # Sin previsualización lista no se puede confirmar
    pending = await client.put(url, json=body, headers=course["headers"])
    assert pending.status_code == 409

    async with sessionmaker() as db:
        await claim_next(db)
        job = await db.get(ImportJob, job_id)
        job.status, job.finished_at = JobStatus.DONE, utcnow()
        await db.commit()

    # Si guardar las notas falla, la importación sigue sin confirmar
    bad = {**body, "grades": [{"student_id": 999_999, "term": 1, "value": 15}]}
    assert (await client.put(url, json=bad, headers=course["headers"])).status_code == 404
    detail = await client.get(f"/teacher/import-jobs/{job_id}", headers=course["headers"])
    assert detail.json()["confirmed_at"] is None

    assert (await client.put(url, json=body, headers=course["headers"])).status_code == 200
    detail = (await client.get(f"/teacher/import-jobs/{job_id}", headers=course["headers"])).json()
    assert detail["attempts"] == 1
    assert detail["confirmed_at"] is not None

    # Un id de importación ajeno o inexistente no se acepta
    unknown = {**body, "import_job_id": "no-existe"}
    assert (await client.put(url, json=unknown, headers=course["headers"])).status_code == 404


async def test_guardar_notas_sin_importacion_sigue_igual(client, course):
    url = f"{course['url']}/grades"
    ana = course["students"]["Ana Pérez"]
    response = await client.put(
        url,
        json={"grades": [{"student_id": ana, "term": 2, "value": 11.5}]},
        headers=course["headers"],
    )
    assert response.status_code == 200


async def test_no_confirma_importaciones_de_otra_materia(client, course, sessionmaker):
    job_id = (await upload(client, course)).json()["id"]
    async with sessionmaker() as db:
        job = await db.get(ImportJob, job_id)
        job.status = JobStatus.DONE
        await db.commit()
        with pytest.raises(ConflictError, match="otra materia"):
            await mark_confirmed(
                db, job.teacher_id, course["subject_id"] + 1, course["section_id"], job_id
            )
        with pytest.raises(ConflictError, match="otra materia o sección"):
            await mark_confirmed(
                db, job.teacher_id, course["subject_id"], course["section_id"] + 1, job_id
            )
