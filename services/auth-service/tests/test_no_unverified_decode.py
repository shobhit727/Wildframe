"""Guard against token decoding that skips signature verification.

``TokenManager.extract_user_id`` used to decode a JWT with
``verify_signature: False`` and hand back the ``user_id`` claim from a token
whose signature was never checked. It had no production callers, so it was
removed rather than fixed -- the verified path is
``TokenManager.verify_token`` / ``_verify_via_jwks``, and a second copy of that
logic is how the two drift apart.

These tests assert on the *unsafe construct* (a ``verify_signature`` flag that
is not ``True``) rather than on any one function name, so reintroducing it
under a different name still fails. A ``not hasattr(TokenManager,
"extract_user_id")`` assertion would pass again the moment the helper was
renamed.
"""

import ast
from pathlib import Path

import app.security
import pytest

APP_DIR = Path(app.security.__file__).resolve().parent.parent

# The key can be passed either as a keyword argument or inside an ``options``
# dict, and either as a bare name or as a string literal.
_VERIFY_SIGNATURE_KEY = "verify_signature"


def _is_truthy_literal(node: ast.expr) -> bool:
    """True only for a literal ``True`` (or ``1``).

    A name, an attribute, or anything computed is treated as *not* proven-safe,
    so ``verify_signature=some_flag`` is rejected. Reviewers have to spell out
    the literal ``True`` to opt back in.
    """
    return isinstance(node, ast.Constant) and bool(node.value) is True


def _disabled_verify_signature_calls(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, description)`` for every signature-skipping call."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # jwt.decode(token, "", options={"verify_signature": False})
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == _VERIFY_SIGNATURE_KEY
                    and not _is_truthy_literal(value)
                ):
                    found.append((key.lineno, "options dict"))

        # jwt.decode(token, "", verify_signature=False)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == _VERIFY_SIGNATURE_KEY and not _is_truthy_literal(keyword.value):
                    found.append((keyword.value.lineno, "keyword argument"))

    return found


def _app_source_files() -> list[Path]:
    files = sorted(APP_DIR.rglob("*.py"))
    assert files, f"no Python sources found under {APP_DIR}; the scan is broken"
    return files


def test_app_dir_is_the_expected_package() -> None:
    """The scan is only meaningful if it is pointed at the real package."""
    assert (APP_DIR / "security" / "__init__.py").is_file()
    assert (APP_DIR / "main.py").is_file()


def test_no_signature_verification_is_disabled_anywhere_in_app() -> None:
    """No file under ``app/`` may decode a token without verifying it."""
    offenders: list[str] = []
    for path in _app_source_files():
        for lineno, kind in _disabled_verify_signature_calls(path):
            rel = path.relative_to(APP_DIR)
            offenders.append(
                f"{rel}:{lineno}: {_VERIFY_SIGNATURE_KEY} is not True ({kind}); "
                "use TokenManager.verify_token instead"
            )

    assert not offenders, "signature verification disabled in:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            "from jose import jwt\n"
            "jwt.decode(token, '', algorithms=['RS256'],"
            " options={'verify_signature': False})",
            id="options-dict-false",
        ),
        pytest.param(
            "from jose import jwt\n" "jwt.decode(token, '', verify_signature=False)",
            id="keyword-false",
        ),
        pytest.param(
            "from jose import jwt\n"
            "jwt.decode(token, '', options={'verify_signature': some_flag})",
            id="options-dict-unproven",
        ),
        pytest.param(
            "def helper(token):\n    return jwt.decode(token, options={" "'verify_signature': 0})",
            id="renamed-helper-zero",
        ),
    ],
)
def test_the_scanner_catches_reintroduced_unsafe_decodes(tmp_path: Path, snippet: str) -> None:
    """The detector must actually fire, including on a renamed helper.

    Without this, a scanner bug that silently matches nothing would make the
    real guard above pass vacuously.
    """
    module = tmp_path / "renamed_helper.py"
    module.write_text(snippet, encoding="utf-8")

    assert _disabled_verify_signature_calls(
        module
    ), "scanner failed to flag an unsafe decode; the real guard is vacuous"


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            "from jose import jwt\n" "jwt.decode(token, jwk, algorithms=['RS256'])",
            id="no-flag",
        ),
        pytest.param(
            "from jose import jwt\n" "jwt.decode(token, jwk, options={'verify_signature': True})",
            id="explicitly-true",
        ),
        pytest.param(
            "from jose import jwt\njwt.get_unverified_header(token)",
            id="unverified-header-is-allowed",
        ),
    ],
)
def test_the_scanner_accepts_the_verified_paths(tmp_path: Path, snippet: str) -> None:
    """The real verifier and header peek must not trip the guard."""
    module = tmp_path / "verified.py"
    module.write_text(snippet, encoding="utf-8")

    assert not _disabled_verify_signature_calls(module)


def test_undecodable_source_is_not_silently_skipped(tmp_path: Path) -> None:
    """A syntax error must raise, not be swallowed into a false pass."""
    module = tmp_path / "broken.py"
    module.write_text("def oops(:\n", encoding="utf-8")

    with pytest.raises(SyntaxError):
        _disabled_verify_signature_calls(module)


def test_extract_user_id_is_gone() -> None:
    """Specific intent behind the broader scan above.

    Deliberately supplementary: the scan is the load-bearing guard, because
    this assertion alone would not notice the helper returning under a new
    name.
    """
    assert not hasattr(app.security.TokenManager, "extract_user_id")
