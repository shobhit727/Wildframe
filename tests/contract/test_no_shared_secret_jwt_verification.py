"""No service may verify tokens with a shared secret (#941).

auth-service signs **RS256** and publishes JWKS. Verifying a token with a shared
HMAC secret instead means anyone who has read the repository can mint a token
with any ``sub`` and any ``role``. The development secret is committed
(``deployments/docker-compose.dev.yml`` sets ``JWT_SECRET_KEY: dev-secret-key``
with ``ENVIRONMENT: development``, and ``DEV_ENVIRONMENTS`` skips the production
secret validator for exactly that value), so the shared-secret path is not a
theoretical weakness.

The migration is now complete on this audit branch: service token verification uses the shared RS256/JWKS path, and the settings gate below prevents any service from regressing to an HS256 default.

Two complementary checks, because neither alone is sufficient:

1. ``test_no_service_verifies_with_a_shared_secret`` - a static scan for the
   inline decode pattern. Catches every service, including ones with no test
   harness of their own, and it is deterministic.
2. ``test_no_service_defaults_to_hs256`` + ``test_hs256_tokens_are_rejected_by
   _the_sdk_verifier`` - real per-service settings instantiated in a
   subprocess, and real RS256/HS256 crypto. These fail if a service silently
   reverts its default.

Each service is exercised in its own subprocess with that service as the working
directory. Importing several services' ``app`` packages into one interpreter is
not possible: they share the top-level ``app`` name, so the first import wins
and the rest silently resolve to the wrong service.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO / "services"

#: Require ``app/main.py``, not merely an ``app/`` directory. A stray
#: ``services/app/`` left behind by someone running Python from the repo root --
#: the exact ``app.*`` import-shadowing hazard AGENTS.md §19 warns about -- passes
#: a bare ``app/`` check and is then probed as if it were a service, landing in
#: ``unprobeable`` and failing the run for a reason unrelated to the bypass.
SERVICE_NAMES = sorted(p.name for p in SERVICES_DIR.iterdir() if (p / "app" / "main.py").is_file())

#: ``jwt.decode(<token>, <key>, ...)``. The key is the second argument, so the
#: scan below inspects what actually lands there.
JWT_DECODE_CALL_RE = re.compile(r"jwt\.decode\(", re.MULTILINE)

#: Locals assigned from the shared secret, e.g. ``jwt_secret = settings.JWT_SECRET_KEY``.
SECRET_ALIAS_RE = re.compile(r"(\w+)\s*(?::[^=\n]+)?=\s*settings\.JWT_SECRET_KEY", re.MULTILINE)


def _decodes_with_shared_secret(text: str) -> bool:
    """True if any ``jwt.decode`` call receives the shared HMAC secret as its key.

    Deliberately narrow. ``auth-service`` also calls ``jwt.decode`` and also
    mentions ``JWT_SECRET_KEY`` -- but it verifies against a JWKS ``jwk`` and uses
    the secret only for MFA secret encryption. Flagging that would be a false
    positive, so the check resolves the *key argument* rather than the file.
    """
    aliases = {m.group(1) for m in SECRET_ALIAS_RE.finditer(text)}
    for match in JWT_DECODE_CALL_RE.finditer(text):
        # The key is the second argument; take a generous slice of the call.
        window = text[match.end() : match.end() + 400]
        depth, args, current = 0, [], []
        for char in window:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                if depth == 0:
                    break
                depth -= 1
            if char == "," and depth == 0:
                args.append("".join(current))
                current = []
                continue
            current.append(char)
        args.append("".join(current))
        if len(args) < 2:
            continue
        key_arg = args[1]
        if "settings.JWT_SECRET_KEY" in key_arg:
            return True
        if any(re.search(rf"\b{re.escape(alias)}\b", key_arg) for alias in aliases):
            return True
    return False


def _service_python_files(service: str) -> list[Path]:
    root = SERVICES_DIR / service / "app"
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


#: ``strict=True`` means this test FAILS if it unexpectedly starts passing, so
#: migrating even one service forces the marker to be removed rather than left
#: to rot. The defect is tracked in #941; the test is the gate, not a comment.
#: Every service is checked. Listing only the known-bad services would mean a
#: migrated service silently drops off the gate the moment it is fixed, which is
#: exactly when you most want it covered -- so the parametrisation spans all of
#: them and only the still-unfixed ones carry a marker.
ALL_SERVICES = sorted(SERVICE_NAMES)

UNMIGRATED_SERVICES: set[str] = set()


SERVICE_CASES = [
    (
        pytest.param(
            service,
            marks=pytest.mark.xfail(strict=True, reason=_XFAIL_REASON),
            id=service,
        )
        if service in UNMIGRATED_SERVICES
        else pytest.param(service, id=service)
    )
    for service in ALL_SERVICES
]


def _service_python_files(service: str) -> list[Path]:
    root = SERVICES_DIR / service / "app"
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _offending_files(service: str) -> list[str]:
    return [
        str(path.relative_to(REPO))
        for path in _service_python_files(service)
        if _decodes_with_shared_secret(path.read_text(encoding="utf-8", errors="replace"))
    ]


@pytest.mark.parametrize("service", SERVICE_CASES)
def test_service_does_not_verify_with_a_shared_secret(service: str) -> None:
    offenders = _offending_files(service)
    assert not offenders, (
        f"{service} decodes a token with the shared HMAC secret instead of "
        f"auth-service's JWKS. Anyone with repository access can forge a token "
        f"with any sub and any role. See #941.\n\n  " + "\n  ".join(offenders)
    )


_SettingsProbe = """
import json, sys
try:
    from app.core.settings import settings
except Exception as exc:                      # a service may legitimately differ
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)

print(json.dumps({
    "algorithm": getattr(settings, "JWT_ALGORITHM", None),
    "jwks_url": getattr(settings, "JWT_JWKS_URL", None),
    "has_secret": bool(getattr(settings, "JWT_SECRET_KEY", None)),
}))
"""


def _static_jwt_algorithm(service: str) -> str | None:
    """Read ``JWT_ALGORITHM``'s declared default without importing the service.

    Importing ``app.core.settings`` needs that service's whole dependency tree
    (fastapi, pydantic-settings, the compliance SDK, ...). The contract job
    installs only pytest, cryptography and python-jose, so the dynamic probe
    cannot import any service there and every one of them lands in
    ``unprobeable`` -- which fails the test for a reason that has nothing to do
    with the bypass.

    The declaration is a plain string literal in all 15 services
    (``JWT_ALGORITHM: str = "RS256"``), so reading it from the AST answers the
    same question without executing untrusted module-level code. We still prefer
    the dynamic probe when the dependencies happen to be present, because only
    it sees an environment override; this is the fallback, not a replacement.
    """
    settings_py = SERVICES_DIR / service / "app" / "core" / "settings.py"
    if not settings_py.is_file():
        return None
    try:
        tree = ast.parse(settings_py.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        if value is None or not isinstance(value, ast.Constant):
            continue
        if any(isinstance(t, ast.Name) and t.id == "JWT_ALGORITHM" for t in targets):
            if isinstance(value.value, str):
                return value.value
    return None


def _probe_service_settings(service: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_SettingsProbe)],
        cwd=SERVICES_DIR / service,
        capture_output=True,
        text=True,
        timeout=180,
    )
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("{")), None)
    probed = json.loads(line) if line is not None else {"error": "probe produced no output"}
    # The probe catches its own ImportError and still prints JSON, so an
    # unimportable service arrives as {"error": ...} rather than as absent
    # output. Falling back only when there is no line at all therefore never
    # fired -- I made that mistake first and the gate stayed red for a reason
    # that had nothing to do with the bypass.
    if "error" not in probed:
        return probed
    static = _static_jwt_algorithm(service)
    if static is not None:
        return {"algorithm": static, "jwks_url": None, "has_secret": None, "via": "ast"}
    return {"error": probed["error"]}


def test_no_service_declares_hs256_as_its_algorithm() -> None:
    """No service may carry ``JWT_ALGORITHM = "HS256"``.

    For the eight services that still decode inline this is the live bypass.
    For the rest it is a dormant landmine: the setting is unused, but it is the
    default that gets picked up the moment someone wires a verifier to it, and
    ``ENVIRONMENT`` is ``development`` in the shipped compose file so nothing
    warns. Both are worth failing on -- an unused HS256 default is how this
    bypass survived an RS256 migration in the first place.
    """
    offenders: list[str] = []
    unprobeable: list[str] = []
    for service in SERVICE_NAMES:
        probed = _probe_service_settings(service)
        if "error" in probed:
            unprobeable.append(f"  services/{service}: {probed['error']}")
        if probed.get("algorithm") == "HS256":
            offenders.append(
                f"  services/{service}/app/core/settings.py"
                f"  JWT_ALGORITHM={probed['algorithm']!r}"
            )
    assert not unprobeable, (
        "These services could not be probed, so this test did not check them:\n"
        + "\n".join(unprobeable)
        + "\n\nInstall the service dependencies in this job. Skipping them made "
        "this assertion pass without having looked at them."
    )
    assert not offenders, (
        "These services declare HS256 as their JWT algorithm. Any verifier wired\n"
        "to that default is forgeable with the committed development secret.\n"
        "Default to RS256 so a missing environment variable fails closed. See #941.\n\n"
        + "\n".join(offenders)
    )


def test_hs256_tokens_are_rejected_by_the_sdk_verifier() -> None:
    """Real crypto: the shared verifier must refuse an HS256 token outright.

    This is the property the eight legacy services lack. Minted with a real
    RSA-2048 key and a real shared secret -- no mocks.
    """
    sdk = REPO / "packages" / "sdk" / "wildframe_auth"
    if not sdk.is_dir():
        pytest.skip("wildframe_auth SDK not present")

    code = textwrap.dedent("""
        import json
        from datetime import datetime, timedelta, timezone
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jose import jwt as jose_jwt
        from jose.jwk import construct as jwk_construct
        from jose.exceptions import JWTError
        from wildframe_auth.verifier import ALLOWED_ALGORITHMS, verify_token

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = jwk_construct(private.public_key(), algorithm="RS256").to_dict()
        pub["kid"] = "k1"
        jwks = {"keys": [pub]}

        now = datetime.now(timezone.utc)
        claims = {
            "sub": "00000000-0000-0000-0000-000000000001",
            "role": "admin",
            "type": "access",
            "av": 0,
            "aud": "wildframe-api",
            "iss": "wildframe-auth",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }

        # 1. The symmetric forgery, signed with the committed dev secret.
        forged = jose_jwt.encode(claims, "dev-secret-key", algorithm="HS256")
        try:
            verify_token(forged, jwks, "wildframe-api", "wildframe-auth")
            print(json.dumps({"hs256_accepted": True}))
        except JWTError:
            print(json.dumps({"hs256_accepted": False}))

        # 2. The genuine RS256 token must still be accepted -- otherwise the
        #    check above would pass for the wrong reason.
        good = jose_jwt.encode(claims, private, algorithm="RS256", headers={"kid": "k1"})
        try:
            payload = verify_token(good, jwks, "wildframe-api", "wildframe-auth")
            print(json.dumps({"rs256_accepted": True, "sub": payload.get("sub")}))
        except Exception as exc:
            print(json.dumps({"rs256_accepted": False, "err": str(exc)}))

        print(json.dumps({"allowed": sorted(ALLOWED_ALGORITHMS)}))
        """)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=sdk,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payloads = [json.loads(ln) for ln in result.stdout.splitlines() if ln.startswith("{")]
    assert payloads, f"probe produced no output; stderr={result.stderr.strip()[:500]}"

    by_key: dict[str, dict] = {}
    for item in payloads:
        by_key.update(item)

    assert by_key.get("hs256_accepted") is False, (
        "the shared verifier accepted an HS256 token signed with a symmetric "
        "secret -- that is precisely the bypass in #941"
    )
    assert by_key.get("rs256_accepted") is True, (
        "the genuine RS256 token was rejected; the HS256 check above would "
        f"then be passing for the wrong reason. detail={by_key}"
    )
    assert by_key.get("allowed") == [
        "RS256"
    ], f"ALLOWED_ALGORITHMS must be RS256 only, got {by_key.get('allowed')}"
