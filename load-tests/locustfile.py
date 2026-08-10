"""Locust load tests for the Wildframe API gateway.

Simulates realistic viewer traffic against the dev stack:

    docker compose -f deployments/docker-compose.dev.yml up --build -d
    locust -f load-tests/locustfile.py --host http://localhost:8000

Flows (all through the gateway on :8000):
  * health    — GET /health (gateway + service health)
  * login     — POST /auth/api/v1/auth/login, caches the access token
  * browse    — GET /content/api/v1/content (Bearer), scrapes content ids
  * stream    — POST /streaming/api/v1/playback-sessions with a real
                content id from browse (Bearer, self-owned user_id)
  * search    — GET /search/api/v1/search/query?q=... (public)

Useful flags:
  --autostart --autoquit 60 -u 50 -r 10 --html load-test-report.html
"""

import base64
import json
import random
from datetime import UTC, datetime

from locust import HttpUser, between, task

# Seed content ids used when the catalog is empty (browse yields none).
FALLBACK_CONTENT_IDS = [
    "00000000-0000-0000-0000-000000000001",
    "00000000-0000-0000-0000-000000000002",
]

SEARCH_TERMS = ["action", "drama", "comedy", "space", "love", "king"]

EMAIL_DOMAIN = "loadtest.wildframe.local"


def _device_id() -> str:
    """Stable-ish per-task device id so Redis rate limits are not hit."""
    return f"loadtest-{random.randint(1, 100)}"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jwt_sub(access_token: str) -> str | None:
    """Decode the JWT payload's subject claim (JWT is not encrypted)."""
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return str(payload.get("sub") or "")
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


class HealthUser(HttpUser):
    """Watchdog user: pings gateway + service health every few seconds."""

    wait_time = between(1, 3)

    @task
    def health(self):
        with self.client.get("/health", name="gateway /health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"health returned {resp.status_code}")
        with self.client.get(
            "/content/api/v1/content?page=1&page_size=1",
            name="content upstream reachable",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401):
                resp.success()
            else:
                resp.failure(f"content returned {resp.status_code}")


class ViewerUser(HttpUser):
    """Realistic viewer: login -> browse -> stream -> search."""

    wait_time = between(2, 6)
    token: str | None = None
    user_id: str | None = None
    content_ids: list[str] = []

    def login(self) -> bool:
        """Register-free login. Uses a shared seeded user per worker if the
        email exists, otherwise falls back to a fresh registration attempt."""
        email = f"viewer-{self.id}@{EMAIL_DOMAIN}"
        payload = {"email": email, "password": "LoadTest123!", "device_id": _device_id()}
        with self.client.post(
            "/auth/api/v1/auth/login", json=payload, name="POST login", catch_response=True
        ) as resp:
            if resp.status_code == 200:
                body = resp.json()
                token = body.get("access_token")
                if token:
                    self.token = token
                    self.user_id = _jwt_sub(token) or self.id
                    resp.success()
                    return True
                resp.failure("login 200 without access_token")
            elif resp.status_code == 401:
                # Unknown user: try registering once so the session can proceed.
                resp.success()
                return self.register(email)
            else:
                resp.failure(f"login returned {resp.status_code}")
        return False

    def register(self, email: str) -> bool:
        payload = {
            "email": email,
            "password": "LoadTest123!",
            "first_name": f"Load{self.id}",
            "last_name": "Tester",
        }
        with self.client.post(
            "/auth/api/v1/auth/register", json=payload, name="POST register", catch_response=True
        ) as resp:
            if resp.status_code == 201:
                body = resp.json()
                token = body.get("access_token")
                if token:
                    self.token = token
                    self.user_id = _jwt_sub(token) or self.id
                resp.success()
            elif resp.status_code == 409:
                resp.success()  # exists; login next iteration will succeed
            else:
                resp.failure(f"register returned {resp.status_code}")
        return bool(self.token)

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(3)
    def browse_catalog(self):
        if not self.token and not self.login():
            return
        with self.client.get(
            "/content/api/v1/content?page=1&page_size=20",
            headers=self.auth_headers(),
            name="GET browse catalog",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                items = resp.json()
                ids = [str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")]
                if ids:
                    self.content_ids = ids
                resp.success()
            elif resp.status_code == 401:
                self.token = None  # expired; re-login next task
                resp.failure("401 during browse")
            else:
                resp.failure(f"browse returned {resp.status_code}")

    @task(2)
    def start_stream(self):
        if not self.token and not self.login():
            return
        if not self.content_ids:
            self.content_ids = list(FALLBACK_CONTENT_IDS)
        content_id = random.choice(self.content_ids)
        payload = {
            "user_id": self.user_id or self.id,
            "content_id": content_id,
            "device_id": _device_id(),
            "protocol": random.choice(["hls", "dash"]),
            "resolution": "720p",
            "bitrate_kbps": 2500,
        }
        with self.client.post(
            "/streaming/api/v1/playback-sessions",
            json=payload,
            headers=self.auth_headers(),
            name="POST start playback",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                session_id = resp.json().get("id")
                if session_id:
                    self.client.get(
                        f"/streaming/api/v1/playback-sessions/{session_id}",
                        headers=self.auth_headers(),
                        name="GET playback session",
                    )
                resp.success()
            elif resp.status_code == 401:
                self.token = None
                resp.failure("401 during stream start")
            else:
                resp.failure(f"stream start returned {resp.status_code}")

    @task(2)
    def search_content(self):
        q = random.choice(SEARCH_TERMS)
        self.client.get(
            f"/search/api/v1/search/query?q={q}",
            name="GET search query",
            catch_response=True,
        )
