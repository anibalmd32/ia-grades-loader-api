"""Alta masiva de estudiantes desde Excel (coordinación)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status

from app.api.deps import DbSession, require_roles
from app.core.config import get_settings
from app.models import Role
from app.schemas.student_import import (
    StudentImportResult,
    StudentImportRow,
    StudentImportSummary,
)
from app.schemas.user import UserRead
from app.services import student_import

router = APIRouter(
    prefix="/students/import",
    tags=["Estudiantes"],
    dependencies=[Depends(require_roles(Role.COORDINATOR))],
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get(
    "/template",
    summary="Plantilla de Excel para el alta masiva",
    response_class=Response,
    responses={200: {"content": {XLSX: {}}, "description": "Archivo .xlsx"}},
)
async def import_template() -> Response:
    return Response(
        student_import.template(),
        media_type=XLSX,
        headers={"Content-Disposition": 'attachment; filename="plantilla-estudiantes.xlsx"'},
    )


@router.post("", summary="Alta masiva de estudiantes desde Excel")
async def import_students(
    db: DbSession,
    file: Annotated[UploadFile, File(description="Planilla .xlsx")],
    dry_run: Annotated[
        bool, Form(description="true = solo validar (no crea nada); false = crear")
    ] = True,
) -> StudentImportResult:
    """Columnas reconocidas por su encabezado (en la primera hoja): **Nombre**, **Apellido**
    (o una sola columna *Nombre completo*), **Tipo** (V/E, opcional: por defecto V o el que
    traiga la cédula), **Cédula** y **Materias** (códigos separados por comas, opcional).

    Primero envía el archivo con `dry_run=true` para ver qué pasará con cada fila; luego,
    con `dry_run=false`, se crean los estudiantes nuevos (con contraseña generada, que
    **solo se muestra en esa respuesta**) y se matriculan en las materias. Los que ya
    existen solo se matriculan; las filas con errores se omiten.
    """
    max_mb = get_settings().max_upload_size_mb
    content = await file.read(max_mb * 1024 * 1024 + 1)
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"El archivo supera el máximo de {max_mb} MB"
        )
    rows = await student_import.import_students(db, content, dry_run=dry_run)
    items = [
        StudentImportRow(
            row=r.row,
            first_name=r.first_name,
            last_name=r.last_name,
            nationality=r.nationality,
            cedula=r.cedula,
            subject_codes=r.subject_codes,
            status=r.status,
            issues=r.issues,
            user=None if dry_run or r.user is None else UserRead.model_validate(r.user),
            generated_password=r.generated_password,
        )
        for r in rows
    ]
    return StudentImportResult(
        dry_run=dry_run,
        rows=items,
        summary=StudentImportSummary(
            **{s: sum(r.status == s for r in rows) for s in ("new", "existing", "error")}
        ),
    )
