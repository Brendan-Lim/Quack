# Setup and configuration

Use Python 3.11+ (3.12 recommended), Node.js 20.9+, npm and a platform supporting Python's `forkserver`, such as Linux or macOS. Real handwriting recognition requires Meta Model API access. An API key should remain on the backend.

## Local development

```bash
git clone https://github.com/Brendan-Lim/Quack.git
cd Quack
cp .env.example .env
```

Edit `.env` to set `MODEL_API_KEY`, `QUACK_ENV=development` and `QUACK_AUTH_MODE=local`. Set explicit `QUACK_BUDGET_*` ceilings within your actual available credit. Local accounts are labelled development stand-ins. A blank model key allows interface exploration without recognition; a configured key can consume credit when checks run.

```bash
# Terminal 1, from the repository root
cd services/api
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn quack.main:create_app --factory --port 8000

# Terminal 2, from the repository root
cd apps/web
npm ci
npm run dev
```

Open `http://localhost:3000`. Create a local test account, complete the six calibration samples and choose My Notebook or Learn. Use `QUACK_AUTH_MODE=off` for an account-free notebook.

For a tablet on the same network, start Next with `npm run dev -- --hostname 0.0.0.0` and open `http://<computer-lan-address>:3000`. The API can remain on localhost because Next proxies requests. Microphone access requires HTTPS or localhost.

## Configuration

The API reads `.env` at the repository root or `services/api/.env`, plus environment overrides. Next's `API_URL` must be set in the shell or frontend environment. [.env.example](../.env.example) lists placeholders without credentials.

| Variable | Purpose |
|---|---|
| `MODEL_API_KEY` | Backend secret for real Meta inference |
| `MODEL_API_BASE_URL` | Optional endpoint override; default `https://api.meta.ai/v1` |
| `QUACK_ENV` | Defaults to production; development enables local accounts and diagnostic endpoints |
| `QUACK_CORS_ORIGINS` | Exact comma-separated frontend origins |
| `QUACK_AUTH_MODE` | `off`, development-only `local`, or production `supabase` |
| `QUACK_DATABASE_URL` | PostgreSQL connection string; backend secret |
| `QUACK_SQLITE_PATH` | Local SQLite alternative; needs a private durable writable directory |
| `QUACK_LEDGER_PATH` | Private durable model-usage ledger path |
| `QUACK_BUDGET_PROJECT_USD`, `QUACK_BUDGET_HOURLY_USD` | Overall and hourly model-spending ceilings |
| `QUACK_BUDGET_SESSION_USD`, `QUACK_BUDGET_REQUEST_USD` | Per-session and per-request ceilings |
| `QUACK_RATE_LIMIT_PER_MINUTE`, `QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE` | Per-client and global request limits |
| `API_URL` | Backend origin for Next's `/api/*` rewrites, set when building |

Other recognition, confirmation, timeout and personalisation settings are defined in `services/api/quack/config.py`. Preserve conservative verification and confirmation defaults unless a change is tested. Setting all model budgets to zero prevents paid model calls.

## Accounts

Production learning accounts use Supabase Auth. Configure `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `QUACK_DATABASE_URL` on the backend. Configure the frontend's site and confirmation/reset redirect URLs in Supabase. The browser obtains the URL and public key from `/api/auth/config`; database access remains on the backend.

New Supabase projects can publish asymmetric signing keys through JWKS. `SUPABASE_JWT_SECRET` is needed only for legacy shared-secret signing. `SUPABASE_SERVICE_ROLE_KEY` stays server-only and is needed for account deletion to remove the sign-in as well as application data.

The API creates its tables and adds known columns on startup. Back up an existing database before changing versions. PostgreSQL tables enable RLS without public policies. Verify registration, confirmation, token refresh, reset, export and deletion against your configured project; automated stand-ins do not establish hosted-service behaviour.

## Server requirements

The frontend requires a Node-capable Next server supporting rewrites:

```bash
cd apps/web
npm ci
API_URL=https://your-api-host npm run build
npm start
```

The backend requires an ASGI server with Python subprocess support:

```bash
cd services/api
.venv/bin/uvicorn quack.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1
```

Changing `API_URL` requires rebuilding the frontend. Static-only hosting does not supply the current API proxy. Use HTTPS and exact origins; never put model keys or service-role keys in `NEXT_PUBLIC_*` variables.

Use one API process/replica: session state, rate limits, caches and budget reservations are not distributed. The verifier starts killable SymPy processes, so a subprocess-restricted function runtime is unsuitable. Durable PostgreSQL does not make the separate usage ledger durable. Ephemeral SQLite or ledger storage loses persistence on restart.

In production, development trace/usage routes, session inspection and API documentation should be unavailable. Request bodies are capped at 10 MB, audio at 8 MB; proxy timeouts must accommodate recognition. `/api/health` reports whether a key is configured, rather than proving live inference succeeds.

Run the [public-file guard](PUBLICATION.md) before packaging. The root `.dockerignore` limits Docker build contexts to declared application inputs. Do not copy unrelated workspace files into a hosting upload. See [architecture](ARCHITECTURE.md) for storage and recognition boundaries.

## Deployment configuration

These configurations prepare hosting; they do not establish a live deployment or an account's free allowance. Verify the selected account's plan, remaining credits and enforceable spending controls before running cloud builds or deployment commands. Keep model inference disabled until its credit and durable accounting are verified.

For Vercel, use a personal Hobby project with **Root Directory** `apps/web`. The included `vercel.json` selects Next.js and `npm ci`. Set `API_URL` to the verified HTTPS backend origin before building. A rewrite can wait up to Vercel's 120-second external-origin limit; changing Next's timeout cannot increase that provider limit. Keep authentication and account responses uncached. Configure Supabase's Site URL and permitted confirmation/reset redirects for the final frontend origin.

The CPU-only Docker image uses Python 3.12, the locked dependency constraints, a non-root user and one Uvicorn worker:

```bash
docker build -f services/api/Dockerfile -t quack-api .
```

Build from this repository root so its explicit Docker context policy applies. Configure Supabase Auth, PostgreSQL with TLS and exact CORS origins through the hosting provider's secret store. Mount private durable storage at `/data` if using its ledger path. The image defaults every model budget to zero. An ephemeral `/data` is unsuitable for a nonzero budget, and a mounted disk alone does not make in-flight reservations crash-safe or distributed. The generic image does not validate a provider's billing controls.

`services/api/modal_app.py` prepares a narrower **account/readiness deployment only**. It uploads literal allowlisted backend runtime files, requests no GPU, permits at most one container with eight concurrent HTTP requests, scales to zero, and uses a 120-second function timeout. It requires an existing Modal secret named `quack-production` containing:

- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`.
- `QUACK_DATABASE_URL`, including a TLS `sslmode`.
- `QUACK_CORS_ORIGINS`, listing the exact HTTPS frontend origin.

Install the separate local SDK with `python -m pip install -r services/api/requirements-deploy.txt`. After verifying the account-specific financial protections and configuring that secret, the provider command is `modal deploy services/api/modal_app.py` from the repository root. Cloud builds can themselves consume allowance. Do not run it merely to inspect the configuration.

This Modal entry point forces production Supabase authentication and PostgreSQL, clears the model key and locks all model budgets to zero even if secret values try to override them. It creates no volume or scheduled job. Its temporary ledger is acceptable only because it cannot make model calls. It cannot deliver the complete handwriting experience until durable, concurrency-safe spending accounting and authorized live inference are implemented and verified. Do not advertise it as a working AI demo.

The model pipeline remains in the ordinary API entry point. A single replica retains session state only while that container lives; account/learning data uses PostgreSQL, while notebook ink remains in browser storage. `/api/health` reports reachability and key configuration, not database readiness, remaining credit or a successful inference. Verify database access through authenticated account requests and test persistence after a host restart before declaring a deployment ready.
