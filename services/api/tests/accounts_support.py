"""What tests of accounts share: a server with local accounts, and writing that passes for a sample."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from quack.config import Settings
from quack.main import build_services, create_app
from tests.test_pipeline import png


def account_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = {
        "MODEL_API_KEY": "test-key",
        "MODEL_API_BASE_URL": "https://api.test/v1",
        "QUACK_LEDGER_PATH": tmp_path / "usage.jsonl",
        "QUACK_RATE_LIMIT_PER_MINUTE": 100000,
        "QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE": 100000,
        "QUACK_ENV": "development",
        "QUACK_AUTH_MODE": "local",
        "QUACK_SQLITE_PATH": tmp_path / "quack.db",
        "QUACK_READING_CACHE_SIZE": 0,
    }
    # To run the same tests on Postgres, set this to an empty database. Every test starts by emptying it.
    postgres = os.environ.get("QUACK_TEST_DATABASE_URL", "")
    if postgres and "SUPABASE_URL" not in overrides:
        base["QUACK_DATABASE_URL"] = postgres
    return Settings(**(base | overrides), _env_file=None)


@pytest.fixture(autouse=True)
def _empty_postgres() -> None:
    postgres = os.environ.get("QUACK_TEST_DATABASE_URL", "")
    if not postgres:
        return
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(postgres)
    if info.get("dbname") != "quack_accounts_test" or info.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("account tests require a local disposable database named quack_accounts_test")
    with psycopg.connect(postgres, autocommit=True) as connection:
        # Each test's server leaves a connection open, and they'd soon use up the limit.
        connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid()")
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")


@pytest.fixture
def accounts_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(build_services(account_settings(tmp_path))))


def register(client: TestClient, email: str = "a@example.com", password: str = "correct horse") -> dict[str, str]:
    response = client.post("/api/auth/local/register", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"authorization": f"Bearer {response.json()['accessToken']}"}


def strokes(count: int = 12, reach: float = 30) -> list[dict[str, Any]]:
    return [
        {
            "id": f"s{index}",
            "points": [
                {"x": 20 + index * 25 + step * reach / 4, "y": 40 + step * 8, "timestamp": step} for step in range(5)
            ],
            "color": "#262626",
            "width": 3,
            "createdAt": 1,
        }
        for index in range(count)
    ]


def sample(count: int = 12) -> dict[str, Any]:
    return {"strokes": strokes(count), "canvasWidth": 1000, "image": png((400, 80))}


def calibrate(client: TestClient, headers: dict[str, str]) -> None:
    state = client.get("/api/calibration", headers=headers).json()
    for prompt in state["prompts"]:
        assert client.put(f"/api/calibration/samples/{prompt['id']}", json=sample(), headers=headers).status_code == 200
