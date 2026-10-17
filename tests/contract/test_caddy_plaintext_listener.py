"""Contract checks for development Caddy plaintext listener exposure.

Issue #975 documents that a wildcard plain-HTTP listener exposes the full API
on every host interface. Plain HTTP is acceptable only for explicit loopback
development tooling; a wildcard hostname/empty host must never be configured.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
CADDYFILE = REPO / "infrastructure" / "caddy" / "Caddyfile"

# Caddy site blocks may place multiple addresses before the opening brace.
PLAIN_HTTP_SITE_RE = re.compile(r"^\s*(http://[^\{]+)\{\s*$", re.MULTILINE)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def test_plain_http_sites_are_loopback_only() -> None:
    """Reject a wildcard/non-loopback plain-HTTP Caddy binding."""
    text = CADDYFILE.read_text(encoding="utf-8")
    sites = PLAIN_HTTP_SITE_RE.findall(text)

    for site in sites:
        for address in site.split(","):
            target = address.strip()
            parsed = urlsplit(target)

            # A missing hostname is Caddy's wildcard host form, e.g. http://:8080.
            assert parsed.hostname in LOOPBACK_HOSTS, (
                f"plain HTTP must bind only to loopback hosts, got {target!r}"
            )


def test_dev_api_http_listener_is_explicitly_loopback() -> None:
    """Keep the intended local tooling endpoint without exposing it to the LAN."""
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "http://localhost:8080 {" in text
    assert "http://localhost:8080, http://:8080 {" not in text
