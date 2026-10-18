"""Carga datos de demostración completos en una base VACÍA (recién creada o reiniciada).

    python -m app.seed_db                     # contraseña de todas las cuentas: demo1234
    python -m app.seed_db --password otra123  # otra contraseña común
    python -m app.reset_db --yes --seed       # borrar todo y sembrar en un solo paso

Crea una universidad de ejemplo lista para enseñar todas las pantallas:

- Configuración académica (semestres de 3 lapsos, notas del 1 al 20, se aprueba con 10).
- Super admin, 2 coordinadores, 6 profesores y 32 estudiantes (V y E), todos activos y sin
  obligación de cambiar la contraseña (salvo un profesor y un estudiante, para enseñar ese
  flujo, y una cuenta desactivada).
- Dos semestres: 2025-II cerrado (todas las notas, ya no se pueden modificar) y 2026-I en
  curso (notas parciales).
- Asignaciones de profesores, matrículas y notas con todas las situaciones posibles
  (aprobada, reprobada, asegurada, en curso e imposible), con su historial de cambios.
- Importaciones de Excel del profesor de ejemplo: una confirmada, una pendiente de revisar
  (con avisos y errores) y una fallida.
- Conversaciones del estudiante de ejemplo con el asistente.

Los datos son siempre los mismos (semilla fija), así que la demo es reproducible.
"""

import argparse
import asyncio
import random
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.url import engine_url_and_args
from app.models import (
    AcademicSettings,
    ChangeSource,
    ChatChannel,
    Conversation,
    ConversationMessage,
    Enrollment,
    Grade,
    GradeChange,
    ImportJob,
    Installation,
    JobStatus,
    MessageRole,
    Nationality,
    PeriodType,
    Role,
    Semester,
    Subject,
    TeacherSubject,
    User,
)
from app.models.import_job import utcnow
from app.models.installation import INSTALLATION_ID
from app.models.settings import SETTINGS_ID
from app.schemas.academic import SubjectRead
from app.schemas.grades import ImportPreview, ImportRow, ImportSummary, StudentSummary
from app.services.rules import GradingRules, snapshot

DEFAULT_PASSWORD = "demo1234"
INSTITUTION = "Universidad Demo"

ADMIN = ("Admin", "Demo", "admin@demo.edu")
COORDINATORS = [
    ("Gabriela", "Morales", "coordinacion@demo.edu"),
    ("Héctor", "Villalobos", "hector.villalobos@demo.edu"),
]
# Nombre, apellido, email, materias que imparte
TEACHERS = [
    ("Carmen", "Rojas", "profesor@demo.edu", ["MAT-101", "MAT-102"]),
    ("Luis", "Fernández", "luis.fernandez@demo.edu", ["FIS-101", "FIS-102"]),
    ("Mariana", "Salazar", "mariana.salazar@demo.edu", ["PRO-101", "PRO-102"]),
    ("José", "Gutiérrez", "jose.gutierrez@demo.edu", ["QUI-101", "EST-101"]),
    ("Andreína", "Blanco", "andreina.blanco@demo.edu", ["LEN-101", "ING-101"]),
    ("Ricardo", "Méndez", "ricardo.mendez@demo.edu", ["MAT-102", "EST-101"]),
]
CLOSED_SEMESTER = "2025-II"
CURRENT_SEMESTER = "2026-I"
SUBJECTS = {
    CLOSED_SEMESTER: [
        ("MAT-101", "Matemática I"),
        ("FIS-101", "Física I"),
        ("QUI-101", "Química General"),
        ("PRO-101", "Introducción a la Programación"),
        ("LEN-101", "Lenguaje y Comunicación"),
    ],
    CURRENT_SEMESTER: [
        ("MAT-102", "Matemática II"),
        ("FIS-102", "Física II"),
        ("PRO-102", "Programación II"),
        ("EST-101", "Estadística"),
        ("ING-101", "Inglés Técnico"),
    ],
}
# Lapsos ya cargados en el semestre en curso, por materia (el resto aún no tiene nota)
CURRENT_TERMS_LOADED = {"MAT-102": 2, "FIS-102": 2, "PRO-102": 3, "EST-101": 2, "ING-101": 1}

# Estudiante de ejemplo: sus notas están elegidas para enseñar cada situación
DEMO_STUDENT = ("Valentina", "Rodríguez", Nationality.VENEZOLANO, 12345678)
DEMO_STUDENT_GRADES = {
    "MAT-101": [16, 17.5, 18],  # aprobada
    "FIS-101": [12, 14.25, 13],  # aprobada
    "QUI-101": [8, 9.5, 7],  # reprobada
    "PRO-101": [19, 20, 18.5],  # aprobada
    "LEN-101": [15, 13, 16],  # aprobada
    "MAT-102": [18, 19, None],  # asegurada
    "FIS-102": [9, 10.5, None],  # en curso: necesita 10.50 en el lapso 3
    "PRO-102": [16, 17.5, 18],  # aprobada (los tres lapsos cargados)
    "EST-101": [1.5, 2, None],  # imposible: ni con 20 llega
    "ING-101": [14, None, None],  # en curso
}

FIRST_NAMES = [
    "Andrés", "Camila", "Daniel", "Sofía", "Gabriel", "Isabella", "Miguel", "Lucía",
    "Santiago", "Mariana", "Alejandro", "Daniela", "Diego", "Paola", "Jesús", "Andrea",
    "Carlos", "María José", "Luis", "Fabiola", "Manuel", "Rosa", "Eduardo", "Patricia",
    "Ángel", "Verónica", "Rafael", "Natalia", "Samuel", "Yuliana", "Jorge",
]  # fmt: skip
LAST_NAMES = [
    "González", "Pérez", "Hernández", "García", "Martínez", "López", "Ramírez", "Torres",
    "Flores", "Díaz", "Mendoza", "Castillo", "Suárez", "Romero", "Vargas", "Medina",
    "Chacón", "Briceño", "Parra", "Contreras", "Rivas", "Guerrero", "Peña", "Silva",
]  # fmt: skip
# Estudiantes extranjeros (cédula E) y nuevos ingresos (solo cursan el semestre en curso)
FOREIGN_STUDENTS = 4
NEW_STUDENTS = 6
TOTAL_STUDENTS = 32


@dataclass
class SeedSummary:
    password: str
    admin: str = ADMIN[2]
    coordinators: list[str] = field(default_factory=list)
    teachers: list[str] = field(default_factory=list)
    demo_student: str = f"{DEMO_STUDENT[2].value}-{DEMO_STUDENT[3]}"
    students: int = 0
    subjects: int = 0
    enrollments: int = 0
    grades: int = 0
    import_jobs: int = 0
    grade_changes: int = 0
    conversations: int = 0


class NotEmptyError(Exception):
    pass


def _grade(rng: random.Random, ability: float) -> Decimal:
    """Nota realista alrededor de la "habilidad" del estudiante, del 1 al 20."""
    value = min(20.0, max(1.0, rng.gauss(ability, 2.6)))
    # La mayoría en medios puntos; algunas con dos decimales
    step = 0.25 if rng.random() < 0.3 else 0.5
    return Decimal(str(round(value / step) * step)).quantize(Decimal("0.01"))


async def _is_empty(db: AsyncSession) -> bool:
    has_users = await db.scalar(select(exists().where(User.id.is_not(None))))
    has_semesters = await db.scalar(select(exists().where(Semester.id.is_not(None))))
    return not has_users and not has_semesters


async def seed(db: AsyncSession, password: str = DEFAULT_PASSWORD) -> SeedSummary:
    """Siembra la base. Falla con NotEmptyError si ya hay usuarios o semestres."""
    if not await _is_empty(db):
        raise NotEmptyError
    rng = random.Random(2026)
    summary = SeedSummary(password=password)
    password_hash = hash_password(password)
    now = utcnow()

    def user(role: Role, first: str, last: str, **fields) -> User:
        fields.setdefault("must_change_password", False)
        return User(
            role=role, first_name=first, last_name=last, password_hash=password_hash, **fields
        )

    # --- Configuración académica ------------------------------------------------
    settings = await db.get(AcademicSettings, SETTINGS_ID) or AcademicSettings(id=SETTINGS_ID)
    settings.institution_name = INSTITUTION
    settings.period_type = PeriodType.SEMESTER
    settings.terms_count = 3
    settings.term_label = "Lapso"
    settings.term_weights = None
    settings.min_grade = Decimal(1)
    settings.max_grade = Decimal(20)
    settings.passing_grade = Decimal(10)
    settings.grade_decimals = 2
    settings.round_final_grade = False
    settings.document_label = "Cédula"
    settings.assistant_enabled = True
    settings.telegram_enabled = True
    settings.document_types = [
        {"code": "V", "name": "Venezolano"},
        {"code": "E", "name": "Extranjero"},
    ]
    db.add(settings)

    # --- Cuentas ------------------------------------------------------------------
    admin = user(Role.SUPER_ADMIN, ADMIN[0], ADMIN[1], email=ADMIN[2])
    db.add(admin)
    await db.flush()
    db.add(Installation(id=INSTALLATION_ID, super_admin_id=admin.id))

    for first, last, email in COORDINATORS:
        db.add(user(Role.COORDINATOR, first, last, email=email))
        summary.coordinators.append(email)

    teachers: dict[str, User] = {}
    for first, last, email, _ in TEACHERS:
        teachers[email] = user(Role.TEACHER, first, last, email=email)
        summary.teachers.append(email)
    # Un profesor aún con la contraseña inicial: enseña el cambio obligatorio
    teachers["ricardo.mendez@demo.edu"].must_change_password = True
    db.add_all(teachers.values())

    demo = user(
        Role.STUDENT,
        DEMO_STUDENT[0],
        DEMO_STUDENT[1],
        nationality=DEMO_STUDENT[2],
        cedula=DEMO_STUDENT[3],
    )
    students = [demo]
    used_names = {(DEMO_STUDENT[0], DEMO_STUDENT[1])}
    for index in range(TOTAL_STUDENTS - 1):
        while (name := (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))) in used_names:
            pass
        used_names.add(name)
        foreign = index < FOREIGN_STUDENTS
        students.append(
            user(
                Role.STUDENT,
                name[0],
                f"{name[1]} {rng.choice(LAST_NAMES)}",
                nationality=Nationality.EXTRANJERO if foreign else Nationality.VENEZOLANO,
                cedula=rng.randint(81_000_000, 84_999_999)
                if foreign
                else 26_000_000 + index * 7919,
            )
        )
    # Uno pendiente de cambiar la contraseña y uno desactivado
    students[1].must_change_password = True
    students[2].is_active = False
    db.add_all(students)
    summary.students = len(students)

    # --- Semestres, materias y profesores ----------------------------------------
    subjects: dict[str, Subject] = {}
    for semester_name, items in SUBJECTS.items():
        semester = Semester(name=semester_name)
        if semester_name == CLOSED_SEMESTER:
            semester.is_closed = True
            semester.closed_at = now - timedelta(days=30)
            # Al cerrarlo se congelaron sus reglas (las mismas que la configuración)
            semester.rules = snapshot(GradingRules(institution_name=INSTITUTION))
        db.add(semester)
        for code, name in items:
            subjects[code] = Subject(code=code, name=name, semester=semester)
            db.add(subjects[code])
    await db.flush()
    summary.subjects = len(subjects)

    teacher_of: dict[str, User] = {}
    for _, _, email, codes in TEACHERS:
        for code in codes:
            db.add(TeacherSubject(teacher_id=teachers[email].id, subject_id=subjects[code].id))
            teacher_of.setdefault(code, teachers[email])

    # --- Matrículas y notas -----------------------------------------------------
    new_students = set(range(len(students) - NEW_STUDENTS, len(students)))
    enrollment_of: dict[tuple[int, str], Enrollment] = {}
    pending_grades: list[tuple[Enrollment, int, Decimal, User]] = []
    for index, student in enumerate(students):
        # Habilidad media del estudiante: la mayoría aprueba, algunos van flojos
        ability = rng.choice([7.5, 9.5, 11, 12.5, 13.5, 14.5, 15.5, 17])
        for semester_name, items in SUBJECTS.items():
            if semester_name == CLOSED_SEMESTER and index in new_students:
                continue
            for code, _ in items:
                enrollment = Enrollment(student_id=student.id, subject_id=subjects[code].id)
                db.add(enrollment)
                enrollment_of[(student.id, code)] = enrollment
                if student is demo:
                    values = [
                        None if v is None else Decimal(str(v)) for v in DEMO_STUDENT_GRADES[code]
                    ]
                else:
                    loaded = 3 if semester_name == CLOSED_SEMESTER else CURRENT_TERMS_LOADED[code]
                    values = [_grade(rng, ability) if t <= loaded else None for t in (1, 2, 3)]
                for term, value in enumerate(values, start=1):
                    if value is not None:
                        pending_grades.append((enrollment, term, value, teacher_of[code]))
    await db.flush()
    summary.enrollments = len(enrollment_of)
    for enrollment, term, value, teacher in pending_grades:
        db.add(Grade(enrollment_id=enrollment.id, term=term, value=value, updated_by_id=teacher.id))
    summary.grades = len(pending_grades)

    # --- Importaciones de Excel del profesor de ejemplo ---------------------------
    carmen = teachers["profesor@demo.edu"]
    mat102 = subjects["MAT-102"]
    roster = [s for s in students if (s.id, "MAT-102") in enrollment_of]
    grades_by_key = {
        (e.student_id, t): v for e, t, v, _ in pending_grades if e.subject_id == mat102.id
    }

    def import_row(student: User, term: int, value: Decimal, **extra) -> ImportRow:
        return ImportRow(
            sheet="Notas",
            row=extra.pop("row"),
            raw_cedula=student.cedula_display,
            raw_name=f"{student.last_name}, {student.first_name}",
            raw_value=str(value),
            student=StudentSummary.model_validate(student),
            term=term,
            value=value,
            current_value=grades_by_key.get((student.id, term)),
            status=extra.pop("status", "ok"),
            issues=extra.pop("issues", []),
        )

    def preview(rows: list[ImportRow], warnings: list[str], missing: list[User]) -> dict:
        return ImportPreview(
            subject=SubjectRead.model_validate(mat102),
            rows=rows,
            summary=ImportSummary(
                **{s: sum(r.status == s for r in rows) for s in ("ok", "warning", "error")}
            ),
            warnings=warnings,
            students_without_grades=[StudentSummary.model_validate(s) for s in missing],
        ).model_dump(mode="json")

    # 1) Confirmada: las notas del lapso 2 que ya están cargadas
    confirmed_rows = [
        import_row(s, 2, grades_by_key[(s.id, 2)], row=i + 4)
        for i, s in enumerate(roster)
        if (s.id, 2) in grades_by_key
    ]
    for row in confirmed_rows:
        row.current_value = None  # cuando se importó, el lapso 2 estaba vacío
    confirmed_job = ImportJob(
        teacher_id=carmen.id,
        subject_id=mat102.id,
        status=JobStatus.DONE,
        file_name="MAT-102 notas lapso 2.xlsx",
        default_term=2,
        attempts=1,
        result=preview(confirmed_rows, [], []),
        created_at=now - timedelta(days=9, minutes=3),
        started_at=now - timedelta(days=9, minutes=2),
        finished_at=now - timedelta(days=9, minutes=1),
        confirmed_at=now - timedelta(days=9),
    )
    db.add(confirmed_job)

    # 2) Pendiente de revisar: propuesta del lapso 3 con un aviso y dos errores
    proposal = [import_row(s, 3, _grade(rng, 13), row=i + 4) for i, s in enumerate(roster[:-3])]
    proposal[1].raw_cedula = None
    proposal[1].status = "warning"
    proposal[1].issues = ["Identificado solo por el nombre: verifica que sea correcto"]
    proposal.append(
        ImportRow(
            sheet="Notas",
            row=len(roster) + 2,
            raw_cedula="V-30111222",
            raw_name="Pedro Castillo",
            raw_value="15",
            student=None,
            term=3,
            value=Decimal(15),
            current_value=None,
            status="error",
            issues=["La cédula V-30111222 no está matriculada en la materia"],
        )
    )
    proposal.append(
        ImportRow(
            sheet="Notas",
            row=len(roster) + 3,
            raw_cedula=roster[-3].cedula_display,
            raw_name=roster[-3].full_name,
            raw_value="NP",
            student=StudentSummary.model_validate(roster[-3]),
            term=3,
            value=None,
            current_value=None,
            status="error",
            issues=["Nota inválida «NP»: debe ser un número del 1 al 20"],
        )
    )
    db.add(
        ImportJob(
            teacher_id=carmen.id,
            subject_id=mat102.id,
            status=JobStatus.DONE,
            file_name="MAT-102 lapso 3 (borrador).xlsx",
            default_term=3,
            attempts=1,
            result=preview(
                proposal,
                ["La planilla trae una columna «Definitiva» que se ignoró."],
                roster[-2:],
            ),
            created_at=now - timedelta(hours=2, minutes=1),
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=59),
        )
    )

    # 3) Fallida
    db.add(
        ImportJob(
            teacher_id=carmen.id,
            subject_id=subjects["MAT-101"].id,
            status=JobStatus.FAILED,
            file_name="planilla escaneada.xlsx",
            attempts=3,
            error="La planilla no tiene filas con notas que se puedan leer",
            created_at=now - timedelta(days=20),
            started_at=now - timedelta(days=20),
            finished_at=now - timedelta(days=20),
        )
    )
    summary.import_jobs = 3
    await db.flush()

    # --- Historial de notas -------------------------------------------------------
    # Cuándo se cargó cada lapso (días atrás) en cada semestre
    loaded_days_ago = {
        CLOSED_SEMESTER: {1: 150, 2: 110, 3: 70},
        CURRENT_SEMESTER: {1: 40, 2: 12, 3: 2},
    }
    semester_of_subject = {
        s.id: name for name, items in SUBJECTS.items() for s in (subjects[c] for c, _ in items)
    }
    changes = []
    for enrollment, term, value, teacher in pending_grades:
        when = now - timedelta(
            days=loaded_days_ago[semester_of_subject[enrollment.subject_id]][term],
            minutes=rng.randint(0, 600),
        )
        from_import = enrollment.subject_id == mat102.id and term == 2
        changes.append(
            GradeChange(
                enrollment_id=enrollment.id,
                term=term,
                old_value=None,
                new_value=value,
                changed_by_id=teacher.id,
                source=ChangeSource.IMPORT if from_import else ChangeSource.MANUAL,
                import_job_id=confirmed_job.id if from_import else None,
                created_at=confirmed_job.confirmed_at if from_import else when,
            )
        )
    # Correcciones: la nota de Valentina en Física II se cargó mal y se corrigió
    fis102 = enrollment_of[(demo.id, "FIS-102")]
    first = next(c for c in changes if c.enrollment_id == fis102.id and c.term == 2)
    first.new_value = Decimal("9.50")
    changes.append(
        GradeChange(
            enrollment_id=fis102.id,
            term=2,
            old_value=Decimal("9.50"),
            new_value=Decimal("10.50"),
            changed_by_id=teacher_of["FIS-102"].id,
            source=ChangeSource.MANUAL,
            created_at=first.created_at + timedelta(days=1),
        )
    )
    db.add_all(changes)
    summary.grade_changes = len(changes)

    # --- Conversaciones con el asistente ---------------------------------------
    chats = [
        (
            "¿Cómo voy en Física II?",
            now - timedelta(days=1),
            [
                "¿Cómo voy en Física II?",
                "En **Física II** tienes 9 en el Lapso 1 y 10.50 en el Lapso 2, así que tu "
                "promedio actual es 9.75. Todavía puedes aprobar: necesitas sacar al menos "
                "**10.50** en el Lapso 3. ¡Vas a buen ritmo, un empujón más!",
            ],
        ),
        (
            "¿En qué materia voy peor?",
            now - timedelta(hours=5),
            [
                "¿En qué materia voy peor?",
                "Tu materia más complicada ahora mismo es **Estadística**: tienes 1.50 y 2 en "
                "los dos primeros lapsos y, aunque saques 20 en el último, no llegarías a 10. "
                "Te recomiendo hablar con el profesor José Gutiérrez para ver qué opciones "
                "tienes.\n\nEn cambio, **Matemática II** ya la tienes asegurada.",
                "¿Y cuál es mi promedio general?",
                "Tu promedio general, contando todas las materias que ya tienen notas, está "
                "por encima de 13. ¡Buen trabajo!",
            ],
        ),
    ]
    for title, when, messages in chats:
        conversation = Conversation(
            student_id=demo.id,
            channel=ChatChannel.APP,
            title=title,
            created_at=when,
            updated_at=when + timedelta(minutes=len(messages)),
        )
        db.add(conversation)
        await db.flush()
        for offset, content in enumerate(messages):
            db.add(
                ConversationMessage(
                    conversation_id=conversation.id,
                    role=MessageRole.USER if offset % 2 == 0 else MessageRole.ASSISTANT,
                    content=content,
                    created_at=when + timedelta(minutes=offset),
                )
            )
    summary.conversations = len(chats)

    await db.commit()
    return summary


def print_summary(summary: SeedSummary) -> None:
    print("Datos de demostración cargados:")
    print(
        f"  {summary.students} estudiantes, {summary.subjects} materias, "
        f"{summary.enrollments} matrículas, {summary.grades} notas "
        f"({summary.grade_changes} cambios en el historial), "
        f"{summary.import_jobs} importaciones, {summary.conversations} conversaciones"
    )
    print(f"\nContraseña de todas las cuentas: {summary.password}\n")
    print(f"  Super admin   {summary.admin}")
    print(f"  Coordinación  {', '.join(summary.coordinators)}")
    print(f"  Profesor      {summary.teachers[0]}  (tiene importaciones de Excel)")
    print(f"  Otros prof.   {', '.join(summary.teachers[1:])}")
    print(f"  Estudiante    {summary.demo_student}  (login con cédula: V y 12345678)")


async def _seed_database(url: str, connect_args: dict, password: str) -> SeedSummary:
    engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            return await seed(db, password)
    finally:
        await engine.dispose()


def run(password: str = DEFAULT_PASSWORD) -> int:
    url, connect_args = engine_url_and_args(get_settings().database_url)
    parsed = make_url(url)
    print(f"Base de datos: {parsed.database} en {parsed.host or 'local'}")
    try:
        summary = asyncio.run(_seed_database(url, connect_args, password))
    except NotEmptyError:
        print(
            "La base ya tiene datos: el seeder solo trabaja sobre una base vacía.\n"
            "Para empezar de cero: python -m app.reset_db --yes --seed"
        )
        return 1
    print_summary(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga datos de demostración completos.")
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"contraseña de todas las cuentas (por defecto {DEFAULT_PASSWORD})",
    )
    args = parser.parse_args(argv)
    if len(args.password) < 8:
        parser.error("la contraseña debe tener al menos 8 caracteres")
    return run(args.password)


if __name__ == "__main__":
    sys.exit(main())
