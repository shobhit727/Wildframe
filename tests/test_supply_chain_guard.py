from datetime import date, timedelta
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/verify-supply-chain.py"
SPEC = importlib.util.spec_from_file_location("verify_supply_chain", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def check_entry(monkeypatch, tmp_path, entry):
    ignore_file = tmp_path / ".trivyignore"
    ignore_file.write_text(entry + "\n", encoding="utf-8")
    monkeypatch.setattr(GUARD, "REPO", tmp_path)
    monkeypatch.setattr(GUARD, "TRIVYIGNORE", ignore_file)
    return GUARD.check_trivyignore()[0]


def test_accepts_future_owned_suppression(monkeypatch, tmp_path):
    expiry = (date.today() + timedelta(days=1)).isoformat()
    assert check_entry(
        monkeypatch,
        tmp_path,
        f"CVE-2026-12345, {expiry}, @security-owner, Reviewed exception",
    ) == []


def test_rejects_expired_suppression(monkeypatch, tmp_path):
    expiry = (date.today() - timedelta(days=1)).isoformat()
    violations = check_entry(
        monkeypatch,
        tmp_path,
        f"CVE-2026-12345, {expiry}, @security-owner, Reviewed exception",
    )
    assert any("expired" in violation for violation in violations)


def test_rejects_malformed_expiry_and_missing_owner(monkeypatch, tmp_path):
    violations = check_entry(
        monkeypatch, tmp_path, "CVE-2026-12345, tomorrow, owner, Reviewed exception"
    )
    assert any("invalid expiry" in violation for violation in violations)
    assert any("owner must" in violation for violation in violations)


def test_rejects_wildcard_cve_suppression(monkeypatch, tmp_path):
    expiry = (date.today() + timedelta(days=1)).isoformat()
    violations = check_entry(
        monkeypatch,
        tmp_path,
        f"CVE-2026-*, {expiry}, @security-owner, Broad exception",
    )
    assert any("wildcard" in violation for violation in violations)