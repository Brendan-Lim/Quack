"""Run each verification in its own process, which can be killed.

SymPy can't be interrupted, and a thread can't be killed. Parser limits make slow inputs
unlikely, but they can't prove that nothing is slow, and this runs on text from a model. Giving
each job a process means that killing a stuck one can't touch anybody else's, and its time limit
starts when it starts, so waiting behind other people's work can't use it up.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import resource
import threading
from multiprocessing.connection import Connection

from quack.services.math_verifier import (
    LineInput,
    VerificationReport,
    completes_answer,
    completes_factorisation,
    completes_in_form,
    verify_lines,
    where_still_present,
)

log = logging.getLogger("quack.verifier_pool")

WORKERS = 2
MEMORY_LIMIT_BYTES = 2 * 1024**3
# A backstop for a worker whose parent died without killing it.
CPU_LIMIT_S = 30
START_TIMEOUT_S = 60.0
_READY = "ready"


class VerificationFailed(Exception):
    """The verifier timed out or crashed. The caller should abstain."""


def _limit_resources() -> None:
    for limit, value in ((resource.RLIMIT_AS, MEMORY_LIMIT_BYTES), (resource.RLIMIT_CPU, CPU_LIMIT_S)):
        try:
            _, hard = resource.getrlimit(limit)
            resource.setrlimit(limit, (value, hard))
        except (ValueError, OSError):
            # Not every platform enforces these. The timeout still applies.
            pass


def _run(
    lines: list[LineInput], acknowledged: frozenset[str]
) -> tuple[VerificationReport, frozenset[tuple[str, int]]]:
    return verify_lines(lines, acknowledged), where_still_present(lines, acknowledged)


def _completes(question: str, expected: str, line: str, form: str = "value") -> bool:
    if form == "factorised":
        return completes_factorisation(question, line)
    if form in ("lowest_terms", "decimal"):
        return completes_in_form(question, expected, line, form)
    return completes_answer(question, expected, line)


def _child(conn: Connection, job: object, *args: object) -> None:
    _limit_resources()
    conn.send(_READY)
    try:
        conn.send(("ok", job(*args)))  # type: ignore[operator]
    except BaseException as exc:  # noqa: BLE001
        conn.send(("error", repr(exc)))
    finally:
        conn.close()


class VerifierPool:
    def __init__(self) -> None:
        self._slots = threading.BoundedSemaphore(WORKERS)
        self._lock = threading.Lock()
        self._context: multiprocessing.context.ForkServerContext | None = None

    def _get_context(self) -> multiprocessing.context.ForkServerContext:
        with self._lock:
            if self._context is None:
                self._context = multiprocessing.get_context("forkserver")
                # Importing SymPy once in the fork server makes starting a worker cheap.
                self._context.set_forkserver_preload(["quack.services.math_verifier"])
            return self._context

    def _run_in_process(
        self, lines: list[LineInput], acknowledged: frozenset[str], timeout_s: float
    ) -> tuple[VerificationReport, frozenset[tuple[str, int]]]:
        return self._run_job(_run, (lines, acknowledged), timeout_s)

    def _run_job(self, job: object, args: tuple[object, ...], timeout_s: float):  # type: ignore[no-untyped-def]
        with self._slots:
            context = self._get_context()
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=_child, args=(sender, job, *args), daemon=True)
            process.start()
            sender.close()
            try:
                # Starting the first worker has to import SymPy, which doesn't count against the limit.
                if not receiver.poll(START_TIMEOUT_S):
                    raise VerificationFailed("timeout")
                receiver.recv()
                if not receiver.poll(timeout_s):
                    log.warning("verification_timeout")
                    raise VerificationFailed("timeout")
                status, payload = receiver.recv()
            except EOFError as exc:
                log.warning("verifier_worker_died")
                raise VerificationFailed("worker_died") from exc
            finally:
                receiver.close()
                process.kill()
                process.join(1)
        if status != "ok":
            # A bug in checking one line shouldn't take down analysis. Quack just stays quiet.
            log.error("verification_error", extra={"error": payload})
            raise VerificationFailed("error")
        return payload

    def warm(self) -> None:
        """Start the fork server now, so the first student's check doesn't wait for SymPy to load."""

        try:
            self._run_in_process([], frozenset(), START_TIMEOUT_S)
        except VerificationFailed:
            log.warning("verifier_warmup_failed")

    async def verify(
        self, lines: list[LineInput], acknowledged: frozenset[str], timeout_s: float
    ) -> tuple[VerificationReport, frozenset[tuple[str, int]]]:
        """The report, and where each acknowledged mistake still is, as (fingerprint, line)."""

        try:
            return await asyncio.to_thread(self._run_in_process, lines, acknowledged, timeout_s)
        except VerificationFailed:
            raise
        except Exception as exc:
            log.exception("verification_error")
            raise VerificationFailed("error") from exc


    async def completes(self, question: str, expected: str, line: str, timeout_s: float, form: str = "value") -> bool:
        """Does this line finish the question? Model text gets the same protection here as anywhere.

        `form` is "factorised" for a question that asks for factors, where the value alone isn't enough.
        """

        try:
            return await asyncio.to_thread(self._run_job, _completes, (question, expected, line, form), timeout_s)
        except VerificationFailed:
            return False
        except Exception:
            log.exception("verification_error")
            return False

    async def call(self, job: object, args: tuple[object, ...], timeout_s: float, fallback: object) -> object:
        """Any other SymPy work on text that came from a model, such as naming a misconception.

        `job` must be a module-level function, so the worker can import it. On any failure, `fallback`.
        """

        try:
            return await asyncio.to_thread(self._run_job, job, args, timeout_s)
        except VerificationFailed:
            return fallback
        except Exception:
            log.exception("verification_error")
            return fallback


shared_pool = VerifierPool()
