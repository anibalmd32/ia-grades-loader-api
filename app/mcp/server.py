"""Servidor MCP (FastMCP) con las herramientas de consulta de notas del estudiante.

Se monta en la API en /mcp (transporte Streamable HTTP). Un cliente MCP (p. ej. Claude
Desktop o un agente propio) se conecta enviando el JWT del estudiante como
`Authorization: Bearer <token>`; las herramientas solo devuelven SUS notas.
"""

from datetime import UTC
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.student_tools import TOOLS, SubjectArgs
from app.models import Role
from app.services import auth as auth_service
from app.services import rules

INSTRUCTIONS = (
    "Herramientas para que un estudiante consulte sus propias notas universitarias. "
    "Cada respuesta incluye la escala de notas, los lapsos y la regla de la definitiva que "
    "configuró la institución. Solo dan acceso a las notas del estudiante dueño del token."
)


class StudentTokenVerifier(TokenVerifier):
    """Acepta solo tokens de estudiantes con una sesión válida (misma regla que la API)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        super().__init__()
        self.sessionmaker = sessionmaker

    async def verify_token(self, token: str) -> AccessToken | None:
        async with self.sessionmaker() as db:
            session = await auth_service.user_from_token(db, token)
        if session is None:
            return None
        data, user = session
        if user.role != Role.STUDENT or user.must_change_password:
            return None
        return AccessToken(
            token=token,
            client_id=str(user.id),
            scopes=[],
            expires_at=int(data.expires_at.replace(tzinfo=UTC).timestamp()),
            subject=str(user.id),
            claims={"user_id": user.id},
        )


def _current_student_id() -> int:
    token = get_access_token()
    if token is None or "user_id" not in token.claims:
        raise ToolError("No autenticado: envía el token del estudiante")
    return int(token.claims["user_id"])


def create_mcp(sessionmaker: async_sessionmaker[AsyncSession]) -> FastMCP:
    mcp = FastMCP(
        "ia-grades-loader",
        instructions=INSTRUCTIONS,
        auth=StudentTokenVerifier(sessionmaker),
    )

    async def run(name: str, args: dict[str, Any]) -> dict[str, Any]:
        student_id = _current_student_id()
        async with sessionmaker() as db:
            if not (await rules.get_rules(db)).assistant_enabled:
                raise ToolError("La institución desactivó el asistente de notas")
            return await TOOLS[name].run(db, student_id, args)

    @mcp.tool(name="resumen_de_notas", description=TOOLS["resumen_de_notas"].description)
    async def resumen_de_notas() -> dict[str, Any]:
        return await run("resumen_de_notas", {})

    @mcp.tool(name="notas_de_materia", description=TOOLS["notas_de_materia"].description)
    async def notas_de_materia(
        materia: Annotated[str, Field(description=SubjectArgs.model_fields["materia"].description)],
    ) -> dict[str, Any]:
        return await run("notas_de_materia", {"materia": materia})

    return mcp
