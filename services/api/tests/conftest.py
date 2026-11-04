from pathlib import Path

import pytest

from quack.config import Settings
from tests.accounts_support import _empty_postgres, accounts_client  # noqa: F401


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        MODEL_API_KEY="test-key",
        MODEL_API_BASE_URL="https://api.test/v1",
        QUACK_LEDGER_PATH=tmp_path / "usage.jsonl",
        QUACK_CONFIRM_ALERTS=False,
        QUACK_RATE_LIMIT_PER_MINUTE=1000,
        QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE=1000,
        QUACK_ENV="development",
        QUACK_AUTH_MODE="off",
        _env_file=None,
    )
