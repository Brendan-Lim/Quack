# Release verification and safeguards

## Reproduce the checks

Use Python 3.12, Node 22, PostgreSQL 18 and Gitleaks 8.30.1. From a clean checkout:

```bash
python3.12 -m venv services/api/.venv
services/api/.venv/bin/python -m pip install -r services/api/requirements.lock -r services/api/requirements-tooling.lock -r services/api/requirements-dev.txt
npm ci --prefix apps/web
(cd apps/web && npx playwright install --with-deps chromium)
# Create an empty LOCAL disposable database named quack_ledger_test.
export QUACK_LEDGER_TEST_DATABASE_URL=postgresql://localhost/quack_ledger_test
npm run verify:release
```

The database role needs permission to create a test role for the row-security regression.
Ledger tests truncate only their disposable local database. Never supply production credentials.
`QUACK_TEST_PYTHON` and `QUACK_GITLEAKS` can select explicit tooling paths.
`QUACK_E2E_PORT_SHIFT` shifts the browser suite's ports if other development servers are running.

The command checks dependency consistency, locked inputs, focused tests, the public-file allowlist,
reachable publication history, staged and historical secrets, Ruff, mypy, offline backend tests,
ESLint, TypeScript, frontend units, the production build, compiled browser checks and all existing
browser journeys. It refuses skipped backend tests and unknown pytest settings or markers.
The two paid live tests are deliberately deselected. No model credits are needed.
Release browser verification enables the existing performance and screenshot journeys as well.
JSON reports must contain passing cases and zero skipped, failed or flaky cases. Compiled and
development-server evidence uses separate output directories so the latter cannot erase the former.
Historical prompt comparisons use a classification-only fixture checked against the published
synthetic dataset. It contains no raw model text, usage records, images or private evaluation paths.

Production credentials and API routing overrides are removed from the verification subprocess
environment. Browser journeys use isolated temporary storage, local account stand-ins and a fake
model endpoint. The compiled suite stubs API responses inside the browser. Production debug-tool
restrictions remain covered by the existing compiled tests.

Run advisory checks separately (they need advisory-service network access):

```bash
services/api/.venv/bin/python -m pip_audit --no-deps --disable-pip -r services/api/requirements.lock -r services/api/requirements-tooling.lock
npm audit --prefix apps/web
```

The workflow runs these audits and the complete release command on pull requests and pushes to
`main`, plus manual dispatch. One GitHub-hosted job avoids duplicate branch/PR browser runs and
uses an ephemeral PostgreSQL service. Chromium is installed explicitly; browser traces and
screenshots are retained for seven days after failures. Focused Playwright and Vitest tests fail
locally and in CI. An additional source guard rejects committed `test.only` forms.

Local evidence does not establish hosted CI or deployment health. Check the workflow attached
to the exact reviewed commit before merging. Advisory results are point-in-time observations.

## Accounting: production versus local development

Production uses **PostgreSQL-backed accounting**. Before a model operation, an advisory transaction
lock coordinates independent connections while allocating a durable debit for the complete retry
allowance. The router's attempts share that debit; they do not multiply it again. Account scope
ignores rotating client session ids. Project, account, request and hourly ceilings remain enforced.
Successful operations settle only when every usage receipt is complete and successful. Unknown
usage, timeout, failed retry, cancellation and crash retain the conservative reservation indefinitely.
Cancelled requests also write a usage receipt. Database failures prevent further model operations;
invalid receipts retain the debit and stop subsequent spending. No production spending records
are deleted or reset by verification or deployment.

Local development can use the append-only JSON file ledger. Each retry independently checks
committed spend and reserves its next worst-case cost. Replay validates every incoming record
before adding any of them. Invalid costs, timestamps or session ids fail closed. Write failures
count completed usage in memory and block further spending for that process. Hourly accounting
handles timestamps received out of order. These disk and replay safeguards apply to the file
backend, not to the PostgreSQL implementation. File reservations coordinate only one process;
file accounting must not replace the production transactional ledger.

Retain one production Modal container: notebook analysis sessions, rate windows and other state
remain process-local even though production spending is coordinated by PostgreSQL. Existing
inference quotas are deployment configuration, not test defaults. Do not increase them for a
release. Provider cost estimates include a fixed input allowance and are not provider billing caps.

## Student-facing safety

Handwriting recognition proposes text; the restricted parser and deterministic SymPy verifier
establish mathematical evidence. Runtime response validation rejects unverified or duplicate
mistakes, inconsistent selected mistakes, present/resolved overlap, and alerts without a current
new verified mistake. These checks run with Python assertions disabled and do not turn model
confidence into mathematical proof.

Notebook hydration validates versions, unique stroke ids, finite geometry, points and metadata.
Unreadable or corrupt values remain stored and new writing cannot overwrite them. Cross-tab
version transactions prevent simultaneous first writers and recreation after another tab deletes
notes. Export, deletion and per-account storage separation retain their existing behavior.

The production scheduler still supports checks of newer ink while an older check runs. Aborted
transports cannot deliver late warnings; a watchdog releases checks even if the transport ignores
abort. Cancelled captures finish cleanup before replacement work starts. Existing stale-reply,
account, mastery and practice regressions cover the broader interaction.

Bricolage Grotesque and Figtree are bundled as Latin WOFF2 assets with the existing
[SIL font licenses](licenses/THIRD_PARTY_NOTICES.txt). Their names, weights and CSS variables are
preserved. Builds no longer fetch Google Fonts; compiled browser tests verify same-origin font
requests, loaded faces, responsive rendering and saved-ink recovery.

## HTTP diagnostics

Every response includes a fresh server-generated `X-Request-ID`. Configured CORS origins can read
it. Nested application logs carry the same context. HTTP events contain the route template,
method, status and duration, without literal student paths, query strings, request bodies or auth
headers. Unhandled failures return a generic response; logs contain the exception class and code
locations, not exception messages, source code, handwriting or local variables. API responses use
`Cache-Control: no-store`. The correlation id is independent of the analysis payload's `requestId`.

The Next.js rewrite forwards backend response headers through the same-origin frontend proxy.
Vercel and Modal generate their own platform request identifiers; those are separate from Quack's.

## Limits of the evidence

Synthetic and mocked checks do not establish real handwriting accuracy, physical Apple Pencil or
iPad behavior, production Supabase registration/deletion/recovery flows, personalized recognition
quality, or provider billing. A release must independently inspect actual hosted CI, frontend
and backend source versions, HTTPS, health/readiness, auth guards, proxy behavior and debug-route
restrictions. Deploying the frontend does not deploy Modal. A live paid inference test requires
explicit release-specific authorization. Never exercise destructive database tests in production.
