#!/bin/sh
# Aplica las migraciones pendientes antes de arrancar (RUN_MIGRATIONS=false para omitirlo,
# p. ej. en réplicas adicionales o en el contenedor del worker).
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Aplicando migraciones..."
    alembic upgrade head
fi

exec "$@"
