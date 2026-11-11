# Tests and reproducibility

Quack includes parser and verifier tests, mocked-API integration tests, frontend state tests and browser journeys. Use the dependencies in `services/api/requirements-dev.txt` and `apps/web/package-lock.json`.

## Backend

```bash
cd services/api
.venv/bin/pytest
.venv/bin/ruff check quack tests
```

The default Pytest configuration excludes the `live` marker. HTTP stand-ins exercise mathematical checking, malformed input, confirmation rules, corrections, budgets, account boundaries, lessons and adaptation without real Meta calls.

Installing `requirements-deploy.txt` also enables `tests/test_deployment.py`. These local SDK checks ensure inference stays disabled by default, enabling it requires explicit budgets and a key, and the deployment cannot use development authentication, fall back to SQLite, or accept non-TLS database connections. They do not authenticate with Modal or start cloud builds.

`tests/test_postgres_ledger.py` exercises concurrent connections, durable debits after interrupted operations and restarts, account quotas, database failure and public-role RLS. It requires `QUACK_LEDGER_TEST_DATABASE_URL` to point to an isolated, disposable PostgreSQL database named `quack_ledger_test`; never point it at a hosted application database. These tests clear only their accounting tables in that disposable database.

## Frontend and browser

```bash
cd apps/web
npm test
npx eslint .
npx tsc --noEmit
npx playwright install chromium
npm run test:e2e
npm run build
```

Vitest covers ink, line grouping, scheduling, duck state and other client logic. Playwright starts real local Next and FastAPI servers, isolated databases and a fake Meta server. The notebook project tests account-free use; the accounts project tests onboarding and learning with development accounts. Browser helpers write synthetic strokes through pointer events.

If the default ports are occupied, use `QUACK_E2E_PORT_SHIFT=1000 npm run test:e2e`. Keep local services isolated rather than stopping unrelated processes. Screenshot and performance specs are opt-in, so a normal browser run can report them as skipped.

Check production-only restrictions against an actual production build:

```bash
npm run build
npm run test:production
```

This starts `next start` and verifies that `/collect` returns HTTP 404, `?debug` and backtick expose no debugging or fixture export, and ordinary tool shortcuts, drawing and undo/redo still work. Authentication is stubbed in the browser for this test; it does not verify hosted accounts or inference. Set `QUACK_E2E_PORT_SHIFT` if port 3200 is occupied. The regular development suite also verifies that collection and debug export remain available locally.

Synthetic fixtures under `evals/synthetic/` and `evals/dataset/synthetic/` support these tests. Evaluation source also supports methods tests, including calibration metrics. Generated results and collected handwriting belong outside the approved public file set.

### Offline tutoring episodes

From the repository root, using the backend development environment:

```bash
services/api/.venv/bin/python evals/tutoring.py --output /tmp/quack-tutoring.json
# Run the same evaluator against the earlier source version first to capture a baseline.
services/api/.venv/bin/python evals/tutoring.py --output /tmp/quack-after.json --baseline /tmp/quack-before.json
```

This reuses the existing HTTP test doubles with the real parser, isolated verifier, account-owned
state, hints, corrections, recap and practice routes. Four chain-rule episodes and a power-rule
control cover detection through follow-up practice. Five guard cases cover ambiguous readings,
disagreement, malformed output, timeout and zero budget. Results contain synthetic case IDs,
contract checks, synthetic practice questions and mock call counts. Shell model/database settings
are removed and unregistered HTTP calls are refused. No key or real model credits are used.
The comparison rejects changed fixtures, changed evaluator code and missing checks; a failed
episode returns a nonzero exit status. These episodes also run in ordinary backend regression tests.

Chain-rule practice now follows a verified missing-factor pattern from My Mistakes or a recap.
It uses small sine, cosine and exponential questions with linear or power inner functions;
all keys are verified before display. Numerical angle offsets and powers of brackets are outside
this initial question bank. Existing uncertainty, completion and budget rules still apply.
The evaluator checks deterministic contracts and hint spoiler/fidelity filters. Human hint review,
real recognition accuracy, learning gains and production latency/cost require separate evidence.

## What the tests establish

Synthetic ink and scripted readings can verify mathematics, state transitions and UI flow. They do not establish physical Apple Pencil behaviour, real handwriting accuracy or learning outcomes. Supabase stand-ins do not establish correct configuration of a hosted Auth project. Two model readings can agree on a misrecognition.

Live tests and evaluation commands are separate and consume API credit. Run them only with intentional model configuration, available allowance and explicit budget ceilings. No real key is needed for the default suites above.

## Public-file validation

From the repository root, run:

```bash
./scripts/check-publication.sh --history
```

This checks the approved file inventory, source-controlled history, public documentation links and media. See [publication policy](PUBLICATION.md). Run it after staging intended changes and before pushing.
