"""Traduce las opciones SSL que los proveedores de MySQL ponen en la URL.

Aiven, Render, Railway... entregan la URL con `?ssl-mode=REQUIRED` (sintaxis del cliente
de MySQL). asyncmy no entiende ese parámetro y falla al conectar, así que se quita de la
URL y se convierte en un `ssl.SSLContext` que se pasa como `connect_args`.
"""

import ssl
from typing import Any

from sqlalchemy.engine import make_url

SSL_MODE_PARAMS = ("ssl-mode", "ssl_mode", "sslmode")


def _ssl_context(mode: str) -> ssl.SSLContext | None:
    mode = mode.strip().upper().replace("-", "_")
    if mode in ("DISABLED", "DISABLE"):
        return None
    context = ssl.create_default_context()
    if mode in ("VERIFY_IDENTITY", "VERIFY_FULL"):
        return context  # certificado y nombre del servidor verificados
    context.check_hostname = False
    if mode != "VERIFY_CA":
        # REQUIRED / PREFERRED: conexión cifrada sin verificar el certificado, igual que
        # el cliente oficial de MySQL con ssl-mode=REQUIRED
        context.verify_mode = ssl.CERT_NONE
    return context


def engine_url_and_args(database_url: str) -> tuple[str, dict[str, Any]]:
    """Devuelve la URL sin parámetros SSL y los `connect_args` equivalentes."""
    url = make_url(database_url)
    query = dict(url.query)
    modes = [query.pop(name) for name in SSL_MODE_PARAMS if name in query]
    if not modes:
        return database_url, {}
    mode = modes[0] if isinstance(modes[0], str) else modes[0][0]
    clean = url.set(query=query).render_as_string(hide_password=False)
    context = _ssl_context(mode)
    return clean, ({"ssl": context} if context else {})
