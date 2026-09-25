from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_exact_status_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_body_has_no_extra_fields():
    """Gate G2 says 'returns exactly {"status": "ok"}' -- a response_model
    that silently added a field would still look fine in a loose equality
    check on a dict superset, so check the key set explicitly."""
    body = client.get("/health").json()
    assert set(body.keys()) == {"status"}


def test_health_rejects_non_get_methods():
    assert client.post("/health").status_code == 405
    assert client.delete("/health").status_code == 405
