// E2E: a real Slate server (uv run slate serve) on a temp SQLite file, serving the built board
// from web/dist. Run `npm run build` first (npm test does). Chromium only.
import { defineConfig, devices } from '@playwright/test';
import { mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_URL } from './e2e/seed';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const port = new URL(BASE_URL).port;
const dataDir = join(here, '.e2e');
const dbPath = join(dataDir, 'slate-e2e.db');

// One fixed sample zone with daylight saving for the whole run: the server, the Python helpers
// the specs spawn, and the date helpers the specs borrow from the board (e2e/seed.ts).
process.env.SLATE_TIMEZONE ??= 'America/Chicago';

// Fresh database per run. The config is evaluated by the runner and by each worker, so only
// the runner (no TEST_WORKER_INDEX yet) wipes it.
if (!process.env.TEST_WORKER_INDEX) {
  rmSync(dataDir, { recursive: true, force: true });
  mkdirSync(dataDir, { recursive: true });
}


export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? 'github' : 'list',
  globalSetup: './e2e/global-setup.ts',
  use: { baseURL: BASE_URL, trace: 'retain-on-failure' },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Sandboxes with a preinstalled Chromium of another revision point at it here.
        launchOptions: process.env.SLATE_E2E_CHROMIUM
          ? { executablePath: process.env.SLATE_E2E_CHROMIUM }
          : {},
      },
    },
  ],
  webServer: {
    command: `uv run slate migrate && uv run slate serve --port ${port}`,
    cwd: root,
    url: `${BASE_URL}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      SLATE_DATABASE_PATH: dbPath,
      SLATE_STATIC_DIR: join(here, 'dist'),
      SLATE_SCHEDULER_ENABLED: 'false',
      // The whole suite signs in with one token, one test after another, so it reads far more
      // per minute than one person's board does. Five times the default still stops a board
      // that starts asking in a loop.
      SLATE_RATE_LIMIT_READS_PER_MINUTE: '6000',
      SLATE_RATE_LIMIT_WRITES_PER_MINUTE: '1500',
      SLATE_TIMEZONE: process.env.SLATE_TIMEZONE ?? 'America/Chicago',
      // A fixed test-only key, so rows that keep secrets (automation webhooks) work in the e2e.
      SLATE_SECRET_KEY: process.env.SLATE_SECRET_KEY ?? '00'.repeat(16) + '22'.repeat(16),
    },
  },
});
