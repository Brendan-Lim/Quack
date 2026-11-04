"""Local deployment checks. No provider login, cloud builds or live model calls."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("modal", reason="install requirements-deploy.txt to validate Modal configuration")


@pytest.fixture
def deployment(monkeypatch):  # type: ignore[no-untyped-def]
    import quack.main

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-public-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-server-key")
    monkeypatch.setenv("QUACK_DATABASE_URL", "postgresql://example.invalid/quack?sslmode=require")
    monkeypatch.setenv("QUACK_CORS_ORIGINS", "https://quack.example")
    monkeypatch.delenv("QUACK_INFERENCE_ENABLED", raising=False)
    # Return settings without creating a database connection or starting any service.
    monkeypatch.setattr(quack.main, "build_services", lambda settings: settings)
    monkeypatch.setattr(quack.main, "create_app", lambda settings: settings)
    path = Path(__file__).resolve().parents[1] / "modal_app.py"
    spec = importlib.util.spec_from_file_location("quack_modal_deployment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readiness_deployment_cannot_enable_model_spending_or_development(deployment, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MODEL_API_KEY", "test-key-that-must-not-be-used")
    monkeypatch.setenv("QUACK_ENV", "development")
    monkeypatch.setenv("QUACK_AUTH_MODE", "off")
    for scope in ("PROJECT", "HOURLY", "SESSION", "REQUEST"):
        monkeypatch.setenv(f"QUACK_BUDGET_{scope}_USD", "100")
    settings = deployment.api.local()
    assert settings.env == "production"
    assert settings.auth_mode == "supabase"
    assert settings.model_api_key == ""
    assert settings.ledger_backend == "postgres"
    assert settings.budget_project_usd == settings.budget_hourly_usd == 0
    assert settings.budget_session_usd == settings.budget_request_usd == 0


def test_enabling_inference_requires_explicit_budgets_and_model_key(deployment, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QUACK_INFERENCE_ENABLED", "1")
    for scope in ("PROJECT", "HOURLY", "SESSION", "REQUEST"):
        monkeypatch.setenv(f"QUACK_BUDGET_{scope}_USD", "0.25")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        deployment.api.local()
    monkeypatch.setenv("MODEL_API_KEY", "test-only-key")
    settings = deployment.api.local()
    assert settings.ledger_backend == "postgres"
    assert settings.budget_project_usd == 0.25
    monkeypatch.setenv("QUACK_BUDGET_PROJECT_USD", "0")
    with pytest.raises(ValueError, match="QUACK_BUDGET_PROJECT_USD"):
        deployment.api.local()


@pytest.mark.parametrize("url", ["", "sqlite:///temporary.db", "postgresql://example.invalid/quack"])
def test_readiness_deployment_requires_postgres_with_tls(deployment, monkeypatch, url):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QUACK_DATABASE_URL", url)
    with pytest.raises(ValueError, match="QUACK_DATABASE_URL"):
        deployment.api.local()


@pytest.mark.parametrize("origin", ["", "http://localhost:3000", "https://*.example"])
def test_readiness_deployment_requires_exact_https_origins(deployment, monkeypatch, origin):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QUACK_CORS_ORIGINS", origin)
    with pytest.raises(ValueError, match="QUACK_CORS_ORIGINS"):
        deployment.api.local()
