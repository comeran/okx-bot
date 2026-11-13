import pytest
from httpx import ASGITransport, AsyncClient

from src.web.app import create_app


@pytest.mark.asyncio
async def test_health_reports_database_read_and_write() -> None:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.asyncio
async def test_health_is_unavailable_when_database_write_fails(monkeypatch) -> None:
    def fail_probe(self) -> None:
        raise OSError("database is read only")

    monkeypatch.setattr(
        "src.data.repository.Repository.probe_database",
        fail_probe,
        raising=False,
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["database"] == "unavailable"
