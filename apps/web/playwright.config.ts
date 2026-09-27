import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// `playwright.config.ts` is loaded as CommonJS here (apps/web has no
// "type": "module"), so __dirname is the correct way to locate the repo root.
const repoRoot = path.resolve(__dirname, '../..');

/**
 * Self-signed dev certificates.
 *
 * `npm run dev` serves HTTPS with `--experimental-https-key/-cert`, and those
 * PEM files are gitignored — so a clean CI checkout has no certificate and
 * `next dev` exits before Playwright can reach it. The `frontend-e2e` job in
 * .github/workflows/ci-dd.yml has no cert-generation step either, so without
 * the command below the suite dies with
 * "Error: Timed out waiting 300000ms from config.webServer".
 *
 * We generate the certificates here instead: `scripts/generate-dev-certs.sh`
 * is idempotent (it exits early when both files exist) and needs nothing but
 * openssl, which every GitHub runner image ships. That keeps the suite
 * runnable from a bare checkout with no workflow edit.
 */
const CERT_SCRIPT = path.join(repoRoot, 'scripts', 'generate-dev-certs.sh');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'html',
  use: {
    baseURL: 'https://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // The dev certificate is self-signed and covers localhost.
    ignoreHTTPSErrors: true,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    // `bash <script> && npm run dev`: the script is a no-op once the
    // certificates exist, so this is safe to run on every invocation.
    command: `bash ${JSON.stringify(CERT_SCRIPT)} && npm run dev`,
    url: 'https://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 300000,
    env: {
      NODE_TLS_REJECT_UNAUTHORIZED: '0',
      NODE_ENV: 'development',
    },
    ignoreHTTPSErrors: true,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
