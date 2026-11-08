import os
from pathlib import Path

import pytest

from quack.config import Settings
from tests.accounts_support import _empty_postgres, accounts_client  # noqa: F401


def pytest_sessionfinish(session: pytest.Session) -> None:
    if os.environ.get("QUACK_RELEASE_VERIFY") == "1":
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter and reporter.stats.get("skipped"):
            reporter.write_sep("=", "Release verification refuses skipped tests")
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def isolate_mocked_http(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocked HTTP must not depend on a developer's or CI runner's proxy setup."""

    if request.node.get_closest_marker("live") is None:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)


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
