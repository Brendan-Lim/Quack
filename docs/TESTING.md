# Tests and reproducibility

Quack includes parser and verifier tests, mocked-API integration tests, frontend state tests and browser journeys. Use the dependencies in `services/api/requirements-dev.txt` and `apps/web/package-lock.json`.

## Backend

```bash
cd services/api
.venv/bin/pytest
.venv/bin/ruff check quack tests
```

The default Pytest configuration excludes the `live` marker. HTTP stand-ins exercise mathematical checking, malformed input, confirmation rules, corrections, budgets, account boundaries, lessons and adaptation without real Meta calls.

Installing `requirements-deploy.txt` also enables `tests/test_deployment.py`. These local SDK checks ensure the Modal readiness configuration cannot enable live spending, use development authentication, fall back to SQLite, or accept non-TLS database connections. They do not authenticate with Modal or start cloud builds.

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

## What the tests establish

Synthetic ink and scripted readings can verify mathematics, state transitions and UI flow. They do not establish physical Apple Pencil behaviour, real handwriting accuracy or learning outcomes. Supabase stand-ins do not establish correct configuration of a hosted Auth project. Two model readings can agree on a misrecognition.

Live tests and evaluation commands are separate and consume API credit. Run them only with intentional model configuration, available allowance and explicit budget ceilings. No real key is needed for the default suites above.

## Public-file validation

From the repository root, run:

```bash
./scripts/check-publication.sh --history
```

This checks the approved file inventory, source-controlled history, public documentation links and media. See [publication policy](PUBLICATION.md). Run it after staging intended changes and before pushing.
