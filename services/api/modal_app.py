"""CPU-only deployment; live model inference is disabled unless explicitly configured.

Run only after verifying account-specific credit and a zero out-of-pocket spend limit.
See docs/SETUP.md. Importing this module does not deploy or provision resources.
"""

import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import modal

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "services" / "api"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .add_local_file(API / "requirements.lock", "/tmp/quack-requirements.lock", copy=True)
    .pip_install_from_requirements(str(API / "requirements.txt"), extra_options="-c /tmp/quack-requirements.lock")
)
# Upload only literal approved runtime files. Never upload the parent workspace, .env,
# local databases, bytecode, tests or private records, even if they are ignored by Git.
for approved in (ROOT / "scripts" / "public-files.txt").read_text().splitlines():
    if approved.startswith("services/api/quack/"):
        relative = Path(approved).relative_to("services/api")
        image = image.add_local_file(ROOT / approved, f"/srv/{relative.as_posix()}")
image = image.env({"PYTHONPATH": "/srv", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})

app = modal.App("quack-api")


@app.function(
    image=image,
    cpu=1,
    memory=2048,
    region="ap-southeast",
    routing_region="ap-south",
    min_containers=0,
    max_containers=1,
    buffer_containers=0,
    scaledown_window=30,
    timeout=120,
    secrets=[modal.Secret.from_name(
        "quack-production",
        required_keys=[
            "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
            "QUACK_DATABASE_URL", "QUACK_CORS_ORIGINS",
        ],
    )],
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def api():  # type: ignore[no-untyped-def]
    from quack.config import Settings
    from quack.main import build_services, create_app

    inference = os.environ.get("QUACK_INFERENCE_ENABLED") == "1"
    budgets = {}
    for scope in ("PROJECT", "HOURLY", "SESSION", "REQUEST"):
        name = f"QUACK_BUDGET_{scope}_USD"
        value = float(os.environ[name]) if inference else 0.0
        if inference and (not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be an explicit positive, finite allowance")
        budgets[name] = value
    key = os.environ.get("MODEL_API_KEY", "") if inference else ""
    if inference and not key:
        raise ValueError("Enabling inference requires MODEL_API_KEY and verified credit")

    settings = Settings(
        _env_file=None,
        QUACK_ENV="production",
        QUACK_AUTH_MODE="supabase",
        MODEL_API_KEY=key,
        QUACK_LEDGER_BACKEND="postgres",
        QUACK_RATE_LIMIT_PER_MINUTE=20,
        QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE=60,
        **budgets,
    )
    database = urlsplit(settings.database_url)
    if database.scheme not in {"postgres", "postgresql"} or not database.hostname:
        raise ValueError("QUACK_DATABASE_URL must configure durable PostgreSQL")
    if parse_qs(database.query).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("QUACK_DATABASE_URL must require TLS with sslmode")
    origins = settings.cors_origin_list
    if not origins or any(urlsplit(origin).scheme != "https" or "*" in origin for origin in origins):
        raise ValueError("QUACK_CORS_ORIGINS must list exact HTTPS frontend origins")
    return create_app(build_services(settings))
