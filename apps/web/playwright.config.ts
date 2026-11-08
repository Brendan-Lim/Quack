import { defineConfig, devices } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

const venv = "../../services/api/.venv/bin";
// Every port moves by this much, so the tests can run beside servers already on these ports, like the
// Phase 3 iPad server on 3100. `QUACK_E2E_PORT_SHIFT=1000 npm run test:e2e`. helpers.ts reads it too.
const shift = Number(process.env.QUACK_E2E_PORT_SHIFT ?? 0);
const port = (base: number) => base + shift;

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1, // the fake Meta server holds one script at a time
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  outputDir: "test-results/journeys",
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    // The notebook on its own, as it was before accounts, which is what proves that path hasn't moved.
    { name: "notebook", testIgnore: /(accounts|learning|lessons)\.spec/, use: { baseURL: `http://127.0.0.1:${port(3100)}` } },
    { name: "accounts", testMatch: /(accounts|learning|lessons)\.spec/, use: { baseURL: `http://127.0.0.1:${port(3101)}` } },
  ],
  webServer: [
    {
      command: `${venv}/uvicorn fake_meta:app --app-dir tests/e2e --port ${port(8101)} --log-level warning`,
      url: `http://127.0.0.1:${port(8101)}/__stats`,
      reuseExistingServer: false,
    },
    {
      command: `${venv}/uvicorn quack.main:create_app --factory --app-dir ../../services/api --port ${port(8001)} --log-level warning`,
      url: `http://127.0.0.1:${port(8001)}/api/health`,
      reuseExistingServer: false,
      env: {
        MODEL_API_KEY: "fake-e2e-key",
        MODEL_API_BASE_URL: `http://127.0.0.1:${port(8101)}/v1`,
        QUACK_LEDGER_PATH: join(tmpdir(), `quack-e2e-${process.pid}.jsonl`),
        QUACK_RATE_LIMIT_PER_MINUTE: "100000",
        QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE: "100000",
        QUACK_BUDGET_HOURLY_USD: "1000",
        // The fake model's answer depends on the test, not the image, so the same ink must not hit a cached reading.
        QUACK_READING_CACHE_SIZE: "0",
        QUACK_CORS_ORIGINS: `http://127.0.0.1:${port(3100)}`,
        // So that a browser test can see why each line was or wasn't flagged.
        QUACK_ENV: "development",
        QUACK_AUTH_MODE: "off",
      },
    },
    {
      command: `${venv}/uvicorn quack.main:create_app --factory --app-dir ../../services/api --port ${port(8002)} --log-level warning`,
      url: `http://127.0.0.1:${port(8002)}/api/health`,
      reuseExistingServer: false,
      env: {
        MODEL_API_KEY: "fake-e2e-key",
        MODEL_API_BASE_URL: `http://127.0.0.1:${port(8101)}/v1`,
        QUACK_LEDGER_PATH: join(tmpdir(), `quack-e2e-accounts-${process.pid}.jsonl`),
        QUACK_SQLITE_PATH: join(tmpdir(), `quack-e2e-${process.pid}`, "quack.db"),
        QUACK_RATE_LIMIT_PER_MINUTE: "100000",
        QUACK_GLOBAL_RATE_LIMIT_PER_MINUTE: "100000",
        QUACK_BUDGET_HOURLY_USD: "1000",
        QUACK_READING_CACHE_SIZE: "0",
        QUACK_CORS_ORIGINS: `http://127.0.0.1:${port(3101)}`,
        QUACK_ENV: "development",
        QUACK_AUTH_MODE: "local",
      },
    },
    {
      command: `npx next dev --port ${port(3100)} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${port(3100)}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { API_URL: `http://127.0.0.1:${port(8001)}`, NEXT_PUBLIC_QUACK_DEBOUNCE_MS: "400", NEXT_PUBLIC_QUACK_READ_ALL: "1", QUACK_DIST_DIR: ".next-e2e" },
    },
    {
      command: `npx next dev --port ${port(3101)} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${port(3101)}`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { API_URL: `http://127.0.0.1:${port(8002)}`, NEXT_PUBLIC_QUACK_DEBOUNCE_MS: "400", NEXT_PUBLIC_QUACK_READ_ALL: "1", QUACK_DIST_DIR: ".next-e2e-accounts" },
    },
  ],
});
