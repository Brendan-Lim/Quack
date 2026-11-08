"""Run only against a disposable database named quack_ledger_test."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import httpx
import psycopg
import pytest
import respx

from quack.llm.ledger import BudgetExceeded, UsageRecord
from quack.llm.postgres_ledger import PostgresLedger
from quack.llm.router import DEFAULT_ROUTES, MAX_ATTEMPTS, ModelError, ModelRouter, Purpose
from tests.test_release_safety import Answer, completion


@pytest.fixture
def url():  # type: ignore[no-untyped-def]
    value = os.environ.get("QUACK_LEDGER_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("set QUACK_LEDGER_TEST_DATABASE_URL to a disposable quack_ledger_test database")
    info = psycopg.conninfo.conninfo_to_dict(value)
    if info.get("dbname") != "quack_ledger_test" or info.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("ledger tests require a local disposable database named quack_ledger_test")
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


@respx.mock
@pytest.mark.parametrize("failure", ["timeout", "invalid_output"])
async def test_router_retries_share_one_durable_operation_debit(url, settings, monkeypatch, failure):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("quack.llm.router._retry_delay", lambda *_: 0)
    first = httpx.ReadTimeout("slow") if failure == "timeout" else completion("not JSON")
    route = respx.post("https://api.test/v1/chat/completions").mock(side_effect=[first, completion()])
    with ledger(url) as instance:
        router = ModelRouter(settings, instance)
        result = await router.complete_json(
            Purpose.EXPLAIN, [], Answer, session_id="alice:s",
            route=replace(DEFAULT_ROUTES[Purpose.EXPLAIN], retry_timeouts=True),
        )
        assert result.value.answer == "ok"
        assert route.call_count == 2
        debits = instance.database.query("SELECT * FROM quack_model_debits")
        assert len(debits) == 1
        receipts = instance.database.query("SELECT * FROM quack_model_usage")
        assert len(receipts) == 2
        assert {row["debit_id"] for row in receipts} == {debits[0]["id"]}
        assert [row["record"]["attempt"] for row in receipts] == [1, 2]
        # Missing/failed usage cannot refund any part of the original retry allowance.
        assert instance.project_spend > sum(float(row["estimated_usd"]) for row in receipts)


@respx.mock
async def test_router_cancellation_records_receipt_and_retains_reserve_after_restart(url, settings):  # type: ignore[no-untyped-def]
    called = asyncio.Event()

    async def waiting(_):  # type: ignore[no-untyped-def]
        called.set()
        await asyncio.Event().wait()
        return completion()

    respx.post("https://api.test/v1/chat/completions").mock(side_effect=waiting)
    with ledger(url) as instance:
        task = asyncio.create_task(ModelRouter(settings, instance).complete_json(Purpose.HINT, [], Answer, session_id="alice:s"))
        await asyncio.wait_for(called.wait(), timeout=5)
        reserved = instance.project_spend
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receipt = instance.database.one("SELECT record FROM quack_model_usage")["record"]
        assert receipt["error_category"] == "cancelled"
        assert receipt["estimated_usd"] == pytest.approx(reserved / MAX_ATTEMPTS)
    with ledger(url) as restarted:
        assert restarted.project_spend == pytest.approx(reserved)
        assert restarted.session_spend("alice:rotated") == pytest.approx(reserved)


@respx.mock
async def test_failed_durable_reservation_never_contacts_provider(url, settings):  # type: ignore[no-untyped-def]
    route = respx.post("https://api.test/v1/chat/completions").mock(return_value=completion())
    with ledger(url) as instance:
        instance.database.close()
        with pytest.raises(ModelError, match="ledger"):
            await ModelRouter(settings, instance).complete_json(Purpose.HINT, [], Answer)
    assert route.call_count == 0


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "0.1", None])
def test_invalid_receipt_preserves_debit_and_blocks_further_spending(url, bad):  # type: ignore[no-untyped-def]
    with ledger(url) as instance:
        with pytest.raises(BudgetExceeded), instance.reserve("alice:s", .02):
            instance.record(UsageRecord("test", "low", "transcribe", "test", estimated_usd=bad))
        assert instance.project_spend == pytest.approx(.06)
        assert instance.database.one("SELECT COUNT(*) AS n FROM quack_model_usage")["n"] == 0
        with pytest.raises(BudgetExceeded), instance.reserve("bob:s", .001):
            pytest.fail("corrupt receipt did not block spending")
