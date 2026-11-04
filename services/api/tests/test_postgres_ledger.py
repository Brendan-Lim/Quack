"""Run only against a disposable database named quack_ledger_test."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import psycopg
import pytest

from quack.llm.ledger import BudgetExceeded, UsageRecord
from quack.llm.postgres_ledger import PostgresLedger


@pytest.fixture
def url():  # type: ignore[no-untyped-def]
    value = os.environ.get("QUACK_LEDGER_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("set QUACK_LEDGER_TEST_DATABASE_URL to a disposable quack_ledger_test database")
    if psycopg.conninfo.conninfo_to_dict(value).get("dbname") != "quack_ledger_test":
        pytest.fail("refusing ledger tests against a database not named quack_ledger_test")
    ledger = PostgresLedger(value, 1, 1, 1, 1)
    ledger.database.execute("TRUNCATE quack_model_usage, quack_model_debits")
    ledger.database.close()
    return value


@contextmanager
def ledger(url, project=1, account=1, request=1, hourly=1):  # type: ignore[no-untyped-def]
    instance = PostgresLedger(url, project, account, request, hourly)
    try:
        yield instance
    finally:
        instance.database.close()


def test_debit_survives_restarts_and_abandoned_operations(url):  # type: ignore[no-untyped-def]
    with ledger(url) as first, pytest.raises(RuntimeError), first.reserve("alice:first", 0.02):
        raise RuntimeError("simulated interrupted model request")
    with ledger(url) as restarted:
        assert restarted.project_spend == pytest.approx(0.06)
        assert restarted.session_spend("alice:another-client-session") == pytest.approx(0.06)
        assert restarted.session_spend("bob:first") == 0


def test_concurrent_independent_connections_cannot_exceed_project_budget(url):  # type: ignore[no-untyped-def]
    with ledger(url, project=0.1) as one, ledger(url, project=0.1) as two:
        def attempt(instance):  # type: ignore[no-untyped-def]
            try:
                with instance.reserve("alice:first", 0.03):
                    return "allowed"
            except BudgetExceeded:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, [one, two]))
        assert sorted(results) == ["allowed", "blocked"]
        assert one.project_spend == pytest.approx(0.09)


def test_account_limit_cannot_be_reset_with_new_session_ids(url):  # type: ignore[no-untyped-def]
    with ledger(url, account=0.1) as instance:
        with instance.reserve("alice:first", 0.03):
            pass
        with pytest.raises(BudgetExceeded, match="account"), instance.reserve("alice:rotated", 0.01):
            pytest.fail("account quota bypassed")
        with instance.reserve("bob:first", 0.01):
            pass


def test_request_and_hourly_limits_include_the_complete_retry_allowance(url):  # type: ignore[no-untyped-def]
    with ledger(url, request=0.1, hourly=0.1) as instance:
        with pytest.raises(BudgetExceeded, match="request"), instance.reserve("alice:s", 0.04):
            pytest.fail("request quota bypassed")
        with instance.reserve("alice:s", 0.03):
            pass
        with pytest.raises(BudgetExceeded, match="hourly"), instance.reserve("bob:s", 0.01):
            pytest.fail("hourly quota bypassed")


def test_actual_usage_is_audited_without_refunding_the_crash_reserve(url):  # type: ignore[no-untyped-def]
    with ledger(url) as instance:
        with instance.reserve("alice:s", 0.02):
            instance.record(UsageRecord("test", "low", "transcribe", "test", estimated_usd=0.001))
        assert instance.project_spend == pytest.approx(0.06)
        assert instance.database.one("SELECT COUNT(*) AS n FROM quack_model_usage")["n"] == 1


def test_larger_actual_usage_blocks_future_calls(url):  # type: ignore[no-untyped-def]
    with ledger(url, project=0.1) as instance:
        with instance.reserve("alice:s", 0.02):
            instance.record(UsageRecord("test", "low", "transcribe", "test", estimated_usd=0.12))
        with pytest.raises(BudgetExceeded, match="project"), instance.reserve("bob:s", 0.001):
            pytest.fail("actual usage was ignored")


def test_successful_receipts_settle_to_actual_cost_and_survive_restart(url):  # type: ignore[no-untyped-def]
    with ledger(url) as instance, instance.reserve("alice:s", 0.02):
        instance.record(UsageRecord(
            "test", "low", "transcribe", "test", estimated_usd=0.001,
            http_status=200, input_tokens=100, output_tokens=20,
        ))
        # Remains reserved until the complete operation has succeeded.
        assert instance.project_spend == pytest.approx(0.06)
    with ledger(url) as restarted:
        assert restarted.project_spend == pytest.approx(0.001)


def test_unknown_usage_is_never_refunded_even_with_a_success_status(url):  # type: ignore[no-untyped-def]
    with ledger(url) as instance:
        with instance.reserve("alice:s", 0.02):
            instance.record(UsageRecord("test", "low", "transcribe", "test", http_status=200, estimated_usd=0.001))
        assert instance.project_spend == pytest.approx(0.06)


def test_database_failure_prevents_a_model_operation(url):  # type: ignore[no-untyped-def]
    with ledger(url) as instance:
        instance.database.close()
        with pytest.raises(BudgetExceeded, match="ledger"), instance.reserve("alice:s", 0.01):
            pytest.fail("operation began without a durable debit")


def test_quota_and_usage_tables_have_no_public_rls_access(url):  # type: ignore[no-untyped-def]
    with ledger(url) as instance:
        with instance.reserve("alice:s", 0.01):
            instance.record(UsageRecord("test", "low", "transcribe", "test", estimated_usd=0.001))
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("DO $$ BEGIN CREATE ROLE quack_ledger_public; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
            connection.execute("GRANT USAGE ON SCHEMA public TO quack_ledger_public")
            connection.execute("GRANT SELECT ON quack_model_debits, quack_model_usage TO quack_ledger_public")
            connection.execute("SET ROLE quack_ledger_public")
            for table in ("quack_model_debits", "quack_model_usage"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
