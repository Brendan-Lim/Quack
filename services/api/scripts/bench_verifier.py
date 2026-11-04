"""How long checking a page takes, with no model involved. Compares two versions of the code by path.

    PYTHONPATH=<path to the services/api of a version> .venv/bin/python scripts/bench_verifier.py

Only says how much of a check is spent on the mathematics. It knows nothing of handwriting latency.
"""

from __future__ import annotations

import statistics
import time

from quack.services.math_verifier import LineInput, still_present, verify_lines

PAGES = {
    "sum": ["3 * 4 = 10"],
    "derivative": ["d/dx(x^2 + 2x) = 3x + 2"],
    "equation chain": ["3(x + 2) = 15", "3x + 6 = 15", "3x = 9", "x = 3"],
    "ten sums": [f"{n} * {n + 1} = {n * (n + 1) + (n % 2)}" for n in range(2, 12)],
    "expansion": ["(x + 1)(x + 2)", "= x^2 + 3x + 2", "= x^2 + 3x + 3"],
}
INTEGRALS = {
    "integral": ["∫ x dx = 2"],
    "integral right": ["∫ x^2 dx = x^3/3 + C"],
    "definite": ["∫_0^2 2x dx = 4"],
    "trig integral": ["∫ cos(2x) dx = sin(2x)/2 + C"],
    "by parts": ["∫ x e^x dx = x e^x - e^x + C"],
}
RUNS = 30


def time_page(lines: list[str]) -> tuple[float, float]:
    inputs = [LineInput(i, text, continues="no" if i == 0 else "yes") for i, text in enumerate(lines)]
    times = []
    for _ in range(RUNS):
        started = time.perf_counter()
        report = verify_lines(inputs)
        known = frozenset(step.fingerprint for step in report.invalid_steps if step.fingerprint)
        still_present(inputs, known)
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times), max(times)


def main() -> None:
    try:
        from quack.services import integration  # noqa: F401

        pages = {**PAGES, **INTEGRALS}
    except ImportError:
        pages = PAGES
    for name, lines in pages.items():
        median, worst = time_page(lines)
        print(f"{name:16} median {median:7.1f} ms   worst {worst:7.1f} ms")


if __name__ == "__main__":
    main()
