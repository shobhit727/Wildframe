"""Issue #788: committed localhost private key must not be tracked and CI must not suppress it.

- apps/web/certificates/localhost-key.pem must not be tracked
- CI workflow and .trivy.yaml must not skip-files that path
- .gitignore must ignore generated certs
- scripts/generate-dev-certs.sh must generate valid PEM pair with correct SANs and 644 perms
"""

from __future__ import annotations

import subprocess
import stat
from pathlib import Path
import os

REPO = Path(__file__).resolve().parents[2]


def test_private_key_not_tracked():
    result = subprocess.run(
        ["git", "ls-files", "--", "apps/web/certificates/localhost-key.pem"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", "private key should not be tracked by git"


def test_private_cert_not_tracked_or_is_generated():
    # certificate may be tracked or not; but private key must not be.
    # Ensure the key is not tracked (covered above); ensure no PEM files are tracked besides maybe .gitkeep
    result = subprocess.run(
        ["git", "ls-files", "--", "apps/web/certificates/"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    tracked = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # Only .gitkeep and README should be tracked, not *.pem
    for f in tracked:
        assert not f.endswith(".pem"), f"PEM file should not be tracked: {f}"


def test_ci_no_longer_skips_private_key():
    ci = (REPO / ".github/workflows/ci-cd.yml").read_text()
    assert (
        "apps/web/certificates/localhost-key.pem" not in ci
    ), "CI must not skip-files the private key"
    # Also ensure Trivy suppression removed
    trivy_cfg = (REPO / ".trivy.yaml").read_text()
    assert "apps/web/certificates/localhost-key.pem" not in trivy_cfg


def test_gitignore_ignores_certs():
    gitignore = (REPO / ".gitignore").read_text()
    assert "apps/web/certificates/*.pem" in gitignore
    # Ensure the key would be ignored if it existed on disk
    result = subprocess.run(
        ["git", "check-ignore", "-q", "apps/web/certificates/localhost-key.pem"],
        cwd=REPO,
    )
    assert result.returncode == 0, ".gitignore should ignore the generated private key"


def test_generate_script_exists_and_executable():
    script = REPO / "scripts" / "generate-dev-certs.sh"
    assert script.exists(), "generation script must exist"
    assert os.access(script, os.X_OK), "script must be executable"
    text = script.read_text()
    # The SAN list is derived from the host's interfaces at run time; the
    # loopback entries are the only constant part of it. See
    # test_dev_cert_sans.py for the behaviour these assertions used to pin.
    assert "DNS:localhost,IP:127.0.0.1,IP:::1" in text
    assert "chmod 644" in text


def test_generate_script_produces_valid_pem_pair(generate_dev_certs):
    """The script must actually produce the pair it promises.

    This used to run its own inline ``openssl`` call and then assert that the
    SAN it had just passed in appeared in the result -- a test that could not
    fail, and that never ran the script at all. It now runs the real script
    and inspects what came out.
    """
    cert = generate_dev_certs()

    key = cert.key_path
    crt = cert.path
    assert key.exists() and crt.exists()
    # Check PEM headers
    assert "BEGIN PRIVATE KEY" in key.read_text()
    assert "BEGIN CERTIFICATE" in crt.read_text()
    # Check permissions
    assert stat.S_IMODE(key.stat().st_mode) == 0o644
    assert stat.S_IMODE(crt.stat().st_mode) == 0o644
    # Loopback SANs are always present, whatever the host's interfaces are.
    assert "127.0.0.1" in cert.ip_addresses
    assert "::1" in cert.ip_addresses
    assert "localhost" in cert.dns_names
    # The script is idempotent: it skips generation when the pair exists.
    assert "already exist" in (REPO / "scripts" / "generate-dev-certs.sh").read_text() or (
        "skipping" in (REPO / "scripts" / "generate-dev-certs.sh").read_text().lower()
    )
