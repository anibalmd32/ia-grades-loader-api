import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u:p@db.example.com:3306/notas",
        "mysql+pymysql://u:p@db.example.com:3306/notas",
        "MySQL+MySQLdb://u:p@db.example.com:3306/notas",
        "  mysql://u:p@db.example.com:3306/notas  ",
    ],
)
def test_database_url_de_proveedores_usa_asyncmy(url):
    settings = Settings(_env_file=None, database_url=url)
    assert settings.database_url == "mysql+asyncmy://u:p@db.example.com:3306/notas"


@pytest.mark.parametrize(
    "url",
    [
        "mysql+asyncmy://u:p@localhost:3306/notas",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_database_url_con_driver_asincrono_no_cambia(url):
    assert Settings(_env_file=None, database_url=url).database_url == url
