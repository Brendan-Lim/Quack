// LIVE tests against the real Meta Model API. Uses MODEL_API_KEY from .env and spends credit.
// Run with: QUACK_BUDGET_PROJECT_USD=5 npx playwright test -c playwright.live.config.ts
import { defineConfig, devices } from "@playwright/test";

const venv = "../../services/api/.venv/bin";

export default defineConfig({
  testDir: "tests/live",
  timeout: 240_000,
  expect: { timeout: 60_000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:3200", ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } },
  webServer: [
    {
      command: `${venv}/uvicorn quack.main:create_app --factory --app-dir ../../services/api --port 8002 --log-level warning`,
      url: "http://127.0.0.1:8002/api/health",
      reuseExistingServer: false,
      env: { QUACK_CORS_ORIGINS: "http://127.0.0.1:3200" },
    },
    {
      command: "npx next dev --port 3200 --hostname 127.0.0.1",
      url: "http://127.0.0.1:3200",
      reuseExistingServer: false,
      timeout: 120_000,
      // No debounce override: live runs measure the product default.
      env: { API_URL: "http://127.0.0.1:8002", QUACK_DIST_DIR: ".next-e2e" },
    },
  ],
});
