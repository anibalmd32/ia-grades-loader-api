"""Utilidades compartidas por los tests de importación de notas."""

import io

from openpyxl import Workbook

from app.services.import_jobs import claim_next, process_job


class FakeLLM:
    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        if isinstance(self.payload, Exception):
            raise self.payload
        return schema.model_validate(self.payload)


def xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


SAMPLE = xlsx([["Cédula", "Nombre", "L1", "L2"], ["V-1", "Ana Pérez", 15, 12]])


async def upload(client, course, content=SAMPLE, default_term=None, headers=None):
    data = {"default_term": str(default_term)} if default_term else {}
    return await client.post(
        f"{course['url']}/grades/import",
        files={"file": ("notas.xlsx", content)},
        data=data,
        headers=headers or course["headers"],
    )


async def process_queue(sessionmaker, llm) -> None:
    """Hace el trabajo del worker: procesa todo lo pendiente."""
    while True:
        async with sessionmaker() as db:
            job_id = await claim_next(db)
        if job_id is None:
            return
        await process_job(sessionmaker, llm, job_id)


def entry(**fields) -> dict:
    return {"sheet": "Sheet", "row": 2, **fields}
