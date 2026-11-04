import { defineConfig, devices } from "@playwright/test";

// Run after npm run build. This suite uses next start, not the development server.
// Its browser-only auth stub opens the notebook without configuring real accounts or inference.
const port = 3200 + Number(process.env.QUACK_E2E_PORT_SHIFT ?? 0);

export default defineConfig({
  testDir: "tests/production",
  workers: 1,
  retries: 0,
  reporter: "list",
  use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${port}` },
  webServer: {
    command: `npx next start --port ${port} --hostname 127.0.0.1`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
  },
});
