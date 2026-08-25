# Dev verification scripts

Reusable browser/E2E and security probes used during development. These are
**not** part of CI — they drive the live stack and expect it to be up
(`docker compose -f deployments/docker-compose.dev.yml up -d`).

## One-time setup

```bash
cd scripts/dev
npm install                      # installs pinned playwright 1.49.1
npx playwright install chromium  # downloads the browser (~160 MB)
```

## Environment

| Variable      | Default                 | Purpose                     |
| ------------- | ----------------------- | --------------------------- |
| `WF_WEB_URL`  | `https://localhost:3000`| Frontend base URL           |
| `WF_API_URL`  | `https://localhost:8000`| Gateway base URL            |

Self-signed certs are accepted (`ignoreHTTPSErrors`).

## Scripts

| Script            | What it does |
| ----------------- | ------------ |
| `user-journey.js` | Full UX smoke: landing → login → watch → search → my-list → account → billing → admin → logout → re-login. Screenshots land in `.tmp/ux/`. |
| `auth-e2e.js`     | Signup with a strong passphrase + login matrix (LAN host + localhost). |
| `signup-debug.js` | Verbose signup trace: every POST, response bodies, DOM alerts. |
| `shoot-all.js`    | Screenshots every page (root, browse, my-list, account, billing, creator, admin×6, login, signup, watch) + console/HTTP error report. |
| `hack.sh`         | Security probes: auth bypass (no token / tampered / forged / refresh-as-access), privilege escalation, IDOR, SQLi/path-traversal, stored-XSS payload creation. Prints PASS/FAIL per check. |

## Usage

```bash
npm run journey                     # defaults
WF_WEB_URL=https://192.168.1.14:3000 npm run auth
npm run probe                       # API-level security checks
```

Demo credentials come from `scripts/seed_demo.py`
(`demo@wildframe.com` / `DemoPass123!`).

## Headless codec limitation

Playwright's bundled Chromium ships **without proprietary codecs** (H.264/AAC),
so the demo HLS stream cannot decode in these tests — you'll see
`bufferAddCodecError` / `manifestIncompatibleCodecsError`. That is a test-browser
limitation, not an app bug: real Chrome/Edge/Firefox/Safari play the asset fine
(verified: manifest + segments serve 200, hls.js attaches the MSE buffer).
