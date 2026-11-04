"""CPU-only account/readiness deployment; live model inference is deliberately disabled.

Run only after verifying account-specific credit and a zero out-of-pocket spend limit.
See docs/SETUP.md. Importing this module does not deploy or provision resources.
"""

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

    settings = Settings(
        _env_file=None,
        QUACK_ENV="production",
        QUACK_AUTH_MODE="supabase",
        MODEL_API_KEY="",
        QUACK_BUDGET_PROJECT_USD=0,
        QUACK_BUDGET_HOURLY_USD=0,
        QUACK_BUDGET_SESSION_USD=0,
        QUACK_BUDGET_REQUEST_USD=0,
        QUACK_RATE_LIMIT_PER_MINUTE=20,
        QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE=60,
        QUACK_LEDGER_PATH="/tmp/quack-disabled-usage.jsonl",
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
