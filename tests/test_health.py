from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_swagger_disponible():
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
