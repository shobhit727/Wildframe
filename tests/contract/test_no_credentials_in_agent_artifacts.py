"""Guard: no credentials in agent-facing prose.

Why this exists
---------------
On 2026-09-28 an agent posted a working dev-stack login to ``Message-board.md``
in entry ``[M-20260928T1615Z-audit-agent]``: an account email and a plaintext
password, four times, in a table and in a ``curl`` body. Five minutes earlier the
same agent had written, in the same file, that the password was *deliberately not
on the board* precisely because "a working credential would live in permanent git
history" and cited ``AGENTS.md`` §24. The repository is public.

Nothing alerted. ``.github/scripts/verify-supply-chain.py`` passes, and it passes
correctly: it polices action pinning, scanner suppressions, ``.trivyignore``
hygiene, and committed private-key/certificate *artifacts*. A plaintext password
inside a tracked markdown file is none of those things. That gap is what this
file closes.

Scope: why prose, not the whole tree
------------------------------------
The obvious rule -- "strong password literal anywhere in a tracked file" -- was
measured on this tree before being written down. With the final label set it
produces **104 findings across 29 tracked files**, only 4 of which are the
incident:

* **78** are test fixtures (``services/auth-service/tests/`` alone holds most;
  ``test_api.py`` has 23). Test fixtures are *supposed* to contain example
  passwords, and a 78-entry exemption list is a suppression file nobody reads.
* **8** are non-prose source (``load-tests/``, a Pydantic schema, a dev script).
* **18** are in prose, of which 14 are a single documented demo password
  repeated across ``QUICKSTART.md``, ``QUICK_START.md``,
  ``README_COMPLETE.md``, ``TESTING_GUIDE.md``, ``TEST_GUIDE.md``,
  ``docs/API_DOCUMENTATION.md`` and ``docs/CONTRIBUTING.md``, plus one citation
  of a variable name inside the issue that reports it.

A guard that fires 104 times on the existing tree is a guard that gets deleted
within a day, and a deleted guard is worse than no guard because it leaves the
gap looking closed. Scoping to prose takes 104 to 18; the allowlist takes 18 to
4; and those 4 are exactly the lines that need fixing.

So this scans the files agents write *prose into for each other*: every
git-tracked ``.md`` / ``.mdx``. That is 79 files today. It is a superset that
cannot rot the way a hand-maintained path list does, and it contains the actual
attack surface -- the coordination log and the docs corpus. Test fixtures are
out of scope *structurally*, not by a 60-entry exemption list, which is what
makes the remaining allowlist small enough to audit.

How the rule stays quiet
------------------------
A finding needs **both** halves:

1. a credential label (``password``, ``pass``, ``passphrase``, ``pwd``,
   ``secret``, ``api_key``, ``access_key``, ``client_secret``, ``auth_token``)
   immediately followed by a delimiter and a quoted value; and
2. a value that looks like a real password: 12-200 chars, no whitespace, not a
   UUID, not a hex blob, not a slug, not an email, not a placeholder
   (``${VAR}``, ``<var>``, ``XXXX``, ``UPPER_SNAKE``), and mixing upper case,
   lower case, a digit, and a symbol.

Half 2 is what does the work. Prose *about* passwords is everywhere on this
board -- there are dozens of lines discussing password hashing, login flows and
step-up -- and none of them can produce a mixed-case digit-and-symbol literal
next to the word. The doc examples below are the one real false-positive class,
and they are handled by the ledger rather than by loosening the shape test,
because loosening it enough to admit them would also admit real secrets.

Allowlist: a suppression ledger, not an exemption
--------------------------------------------------
``ALLOWLIST`` mirrors the ``.trivyignore`` convention already used in this repo
(``value-id, expiry, owner, justification``), with the same teeth: every entry
must have four fields, a real future ISO date, an ``@handle`` owner, and a
non-empty justification, or the guard fails on its own bookkeeping.

Entries are keyed by ``sha256:<12 hex>`` of the value rather than by the value
itself. Two reasons. A suppression ledger that spells out the password it
suppresses is a second copy of the secret in a second file; and this file must
never itself contain a usable password. The hash is stable, so a suppressed
finding can still be located and reviewed -- the failure output reports the exact
``file:line`` of every suppressed hit.

The one entry below is the documented demo password from issue #805, which
already tracks removing it from operational docs. It is a *known-public demo
value*, and it is acknowledged, not endorsed; it has an expiry so it cannot
outlive the issue.

There is deliberately **no** way to suppress a finding in
``Message-board.md``. History on this branch cannot be rewritten, so anything
allowed to land there is permanent and public forever. ``Message-board.md`` is
checked with zero tolerance, and
``test_allowlist_cannot_suppress_the_board`` enforces that no ledger entry can
be used to route around it.

This test is currently RED, on purpose
--------------------------------------
The credential from ``M-20260928T1615Z-audit-agent`` is still in
``Message-board.md`` at the time of writing; removing it is a separate, pending
decision. A guard that quietly excused a burned credential in a public repo
would be the exact failure mode this file exists to prevent, so it is left
failing until the four lines are redacted. The failure message names the entry
id, the file and the line numbers, and never the value.

Known limits, measured rather than assumed
------------------------------------------
Everything below was counted on this tree, not reasoned about.

* **A credential label is required.** Dropping it and scanning for any strong
  literal in prose produces **258 sites across 190 distinct values**: board entry
  ids, GitHub API URLs, virtualenv paths, ``eyJ...`` JWT prefixes, ``owner/repo``
  slugs. Un-shippable. The label is what makes the rule precise (18 sites), and
  it is the single most important thing in this file. The label set was then
  widened to 20 entries at **zero** measured cost.

* **A delimiter is required** -- ``=`` ``:`` ``=>`` ``->`` ``is``, a quote, or a
  markdown table cell. A value mentioned in running prose with no delimiter,
  e.g. "smoke test login uses ``Hn7#vB2@xQ4m`` for the admin fixture", is not
  detected. Requiring a delimiter keeps ``password: see the docs`` and
  ``passphrase: not configured`` quiet, which is the overwhelming majority of
  label-adjacent text.

* **A label is not the same as the word "password"**, and the rule keys on
  assignment shape rather than on a bare token. A secret dropped as an
  unlabelled opaque string in the middle of a paragraph is out of reach. Closing
  that would need a context-window rule, which was measured at 12 existing
  non-secret sites (paths, URLs, ``NAME=value`` pairs) for a much lower-probability
  case; the narrow label-anchored rule was kept instead.

* **Test fixtures and service source are out of scope by construction.** That is
  a deliberate trade, not an oversight: 78 of the 104 whole-tree findings are in
  test files, and covering them would mean a 78-entry exemption list. The
  consequence is that this guard cannot tell you a fixture password was reused to
  mint a real account -- which is exactly what happened here. Closing *that* gap
  belongs with issue #805, not with this file.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "Message-board.md"

#: Agent-facing prose. A superset, deliberately: no hand-maintained path list.
PROSE_SUFFIXES = (".md", ".mdx")

#: Zero tolerance regardless of the allowlist.
ZERO_TOLERANCE = ("Message-board.md",)

#: Credential labels. Single non-capturing group on purpose: appending a bare
#: ``|foo`` here would escape the group and silently move the trailing
#: ``(...)|`` capture out of scope, after which most forms match a bare word
#: with an unset group. ``test_every_form_exposes_exactly_one_value_group``
#: pins this.
#:
#: The second half (credential|account|log in|sign in|username|token|jwt|key)
#: was added after measuring, and cost zero: the tree stayed at 18 sites / 3
#: distinct values while three more disclosure shapes became detectable,
#: including "service account: `...`" and "the deploy token is `...`".
_LABEL = (
    r"(?:pass(?:word|wd|phrase)?|pwd|passphrase|secret|api[_-]?key"
    r"|access[_-]?key|client[_-]?secret|auth[_-]?token"
    r"|credential|account|log[ -]?in|sign[ -]?in|user[_-]?name"
    r"|api[_-]?token|jwt|key|token)"
)

#: The four ways this repo writes a credential down. All are single-line on
#: purpose: multi-line matching was where precision was lost.
_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # KEY = "v" / KEY: "v" / KEY => "v" / KEY -> "v" / KEY is "v"
    # Covers Python, YAML, shell and TOML assignment, including a value bound to
    # a variable name (PASS='v') rather than written inline at the call site.
    #
    # The lead is a negative lookbehind rather than \b, and the difference is not
    # cosmetic: \b fails on API_SECRET= and DB_PASSWORD=, because "_" is a word
    # character and so no boundary exists before "SECRET". Those SCREAMING_SNAKE
    # env-var names are the single most likely way an agent would write a
    # secret down, and a strict \b silently missed every one of them. Measured
    # cost of loosening: exactly one extra site tree-wide (a citation of the
    # variable inside the issue that tracks it, see ALLOWLIST).
    (
        "assignment",
        re.compile(
            rf"""(?i)(?<![A-Za-z0-9]){_LABEL}\b\s*(?:=|:|=>|->|is)\s*["'`]([^"'`\n]{{6,200}})["'`]"""
        ),
    ),
    # \"password\":\"v\" -- JSON escaped inside a shell -d string, which is how
    # every curl example on this board writes it. A plain \"key\": \"v\" regex
    # misses these entirely because the quotes are backslash-prefixed.
    (
        "escaped-json",
        re.compile('(?i)\\\\"' + _LABEL + '\\\\"\\s*:\\s*\\\\"([^"\\\\\\n]{6,200})\\\\"'),
    ),
    # "password": "v" -- ordinary JSON / YAML request body.
    (
        "json",
        re.compile(rf"""(?i)["']{_LABEL}["']\s*:\s*["']([^"'\n]{{6,200}})["']"""),
    ),
    # | password | `v` | -- markdown table cell. This is the form the incident
    # used to present the value most legibly, and it is not an assignment.
    (
        "table-cell",
        re.compile(rf"""(?i)\|\s*{_LABEL}\s*\|\s*`([^`\n]{{6,200}})`\s*\|"""),
    ),
    # unquoted YAML/env scalar: "JWT_SECRET: Vq9!nHb3vCy6@Ke" running to EOL.
    # The only form here with no quote requirement, so it is the one that could
    # have cost precision. It did not: measured at 0 sites tree-wide, because a
    # value reaching this regex has already survived the four-class test, and
    # prose after a label ("password: see the docs", "passphrase: not
    # configured") fails on whitespace, length, or missing classes.
    (
        "unquoted-scalar",
        re.compile(rf"""(?i)(?<![A-Za-z0-9]){_LABEL}\b\s*:\s*(\S[^\n]{{11,200}})\s*$"""),
    ),
)

_UUID = re.compile(r"(?i)\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_HEX_BLOB = re.compile(r"(?i)\A[0-9a-f+\-]{16,}\Z")
_SLUG = re.compile(r"\A[a-z0-9]+(?:[-_./:@][a-z0-9]+)*\Z")
_EMAIL = re.compile(r"\A[^@\s]+@[^@\s]+\.[A-Za-z]{2,}\Z")
_PLACEHOLDER = re.compile(
    r"\A(?:x{3,}|\*{3,}|\.{3,}|<[^>]+>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[a-z]|[A-Z][A-Z0-9_]{2,})\Z"
)

MIN_LENGTH = 12
MAX_LENGTH = 200


@dataclass(frozen=True)
class Finding:
    """One credential-shaped literal.

    Deliberately carries no value -- only a truncated digest -- so that neither
    the assertion output nor a pytest failure artefact ever contains a secret.
    """

    path: str
    line: int
    form: str
    digest: str

    def where(self) -> str:
        return f"{self.path}:{self.line}"


def fingerprint(value: str) -> str:
    """Stable, non-reversible identifier for a literal."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def is_strong_password_literal(value: str) -> bool:
    """True when *value* could be a real, reusable password.

    Requires all four character classes. A UUID, hex digest, slug, email or
    placeholder is rejected first even though several of those technically mix
    cases and digits, because they are identifiers, not secrets.
    """
    if not MIN_LENGTH <= len(value) <= MAX_LENGTH:
        return False
    if any(c.isspace() for c in value):
        return False
    if _UUID.match(value) or _HEX_BLOB.match(value) or _SLUG.match(value):
        return False
    if _EMAIL.match(value) or _PLACEHOLDER.match(value):
        return False
    return (
        any(c.isupper() for c in value)
        and any(c.islower() for c in value)
        and any(c.isdigit() for c in value)
        and any(not c.isalnum() for c in value)
    )


def find_findings(path: str, text: str) -> list[Finding]:
    """Scan *text* and return every credential-shaped literal, in line order."""
    found: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        for form, pattern in _FORMS:
            for match in pattern.finditer(line):
                value = match.group(1)
                if is_strong_password_literal(value):
                    found.append(Finding(path, number, form, fingerprint(value)))
    return found


def tracked_prose_files() -> list[str]:
    """Tracked markdown, relative to the repo root.

    Tracked-only on purpose. Other agents keep untracked scratch at the repo
    root, and scanning the working tree would make this guard report another
    agent's half-finished notes instead of committed content.
    """
    result = subprocess.run(
        ["git", "ls-files", "--", "*.md", "*.mdx"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"git ls-files failed, cannot enumerate prose: {result.stderr.strip()!r}")
    return sorted(f for f in result.stdout.splitlines() if f.endswith(PROSE_SUFFIXES))


def read_prose(path: str) -> str | None:
    """File contents, or None when unreadable (binary, deleted mid-run)."""
    file = REPO / path
    if not file.is_file():
        return None
    try:
        return file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def scan_prose() -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_prose_files():
        text = read_prose(path)
        if text is None:
            continue
        findings.extend(find_findings(path, text))
    return findings


#: Suppression ledger. Mirrors ``.trivyignore``:
#: ``fingerprint, expiry (YYYY-MM-DD), owner (@handle), justification``.
#: Keyed by digest so this file holds no usable password.
ALLOWLIST: tuple[tuple[str, str, str, str], ...] = (
    (
        "sha256:6faba6e4ed72",
        "2026-12-31",
        "@shobhit727",
        "Documented demo password repeated in 7 operational docs; tracked by "
        "issue #805 (remove plaintext demo passwords from operational docs). "
        "Known-public demo value, not a live credential. Reassess at expiry.",
    ),
    (
        "sha256:f66ab64ee835",
        "2026-12-31",
        "@shobhit727",
        "Citation of the seed script's variable name inside the issue that reports "
        "it, .github/issues/003-hardcoded-secrets-and-demo-credentials.md. A "
        "reference to the name in a finding, not a disclosure of a new value. "
        "Removal tracked by issue #805. Reassess at expiry.",
    ),
)


def suppressed_paths() -> set[str]:
    return {entry[0] for entry in ALLOWLIST}


def is_zero_tolerance(path: str) -> bool:
    return path in ZERO_TOLERANCE


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_no_credentials_in_agent_prose() -> None:
    """No credential-shaped literal in any tracked markdown, board excepted
    from the allowlist entirely.

    Fails on the four lines of the ``M-20260928T1615Z-audit-agent`` credential
    until they are redacted. That is intended: see the module docstring.
    """
    allowed = suppressed_paths()
    findings = scan_prose()

    violations = [f for f in findings if f.digest not in allowed]
    acknowledged = [f for f in findings if f.digest in allowed]

    report_lines = [
        "",
        f"Scanned {len(tracked_prose_files())} tracked markdown files; "
        f"{len(findings)} credential-shaped literal(s), {len(acknowledged)} acknowledged "
        f"by the allowlist, {len(violations)} not.",
        "",
        "UNSUPPRESSED:",
    ]
    for f in violations:
        report_lines.append(f"  {f.where()}  form={f.form}  {f.digest}")
    report_lines += [
        "",
        "Suppression ledger (expiring, owner-attributed):",
    ]
    for digest, expiry, owner, _ in ALLOWLIST:
        sites = [f.where() for f in acknowledged if f.digest == digest]
        report_lines.append(f"  {digest} expires {expiry} {owner} at {len(sites)} site(s)")

    report_lines += [
        "",
        "This repository is PUBLIC. A credential posted here lives in permanent "
        "git history: redaction of the tip does not remove it, because this "
        "branch cannot be rewritten while several agents push to it.",
        "",
        "Do NOT post a login, password, token or shared secret to the board or "
        "to any tracked doc. Mint your own account instead -- see the 'Mint your "
        "own account' recipe in Message-board.md, which needs no shared secret.",
        "",
        "If this value is real rather than a fixture, treat it as burned and "
        "rotate it. Do not add it to ALLOWLIST: the ledger exists for known-public "
        "demo values, and a live credential is never one of those.",
    ]

    assert not violations, "\n".join(report_lines)


def test_allowlist_entries_are_well_formed() -> None:
    """The ledger obeys the same discipline as ``.trivyignore``.

    A suppression that can be added without an owner, a date and a reason is
    just a way to turn the guard off, so the bookkeeping is itself policed.
    """
    today = date.today()
    assert ALLOWLIST, "ALLOWLIST should be declared, even when empty"

    for entry in ALLOWLIST:
        assert len(entry) == 4, f"allowlist entry must have 4 fields, got {len(entry)}: {entry[0]}"
        digest, expiry_text, owner, justification = entry

        assert re.fullmatch(r"sha256:[0-9a-f]{12}", digest), (
            f"allowlist key must be 'sha256:<12 hex>' of the value, not the value "
            f"itself; got {digest!r}. This file must never contain a usable password."
        )
        try:
            expiry = date.fromisoformat(expiry_text)
        except ValueError:
            pytest.fail(
                f"allowlist entry {digest}: invalid expiry {expiry_text!r}, want YYYY-MM-DD"
            )
        assert expiry > today, (
            f"allowlist entry {digest} expired on {expiry_text}. Reassess it and "
            f"re-issue with a new date, or delete it."
        )
        assert re.fullmatch(
            r"@[A-Za-z0-9_.-]+", owner
        ), f"allowlist entry {digest} owner must be an @handle, got {owner!r}"
        assert (
            len(justification.strip()) >= 20
        ), f"allowlist entry {digest} needs a real justification, not {justification!r}"


def test_allowlist_cannot_suppress_the_board() -> None:
    """No ledger entry may cover a finding in ``Message-board.md``.

    Without this, the cheapest way to make a red board green is to add its
    credential to the allowlist, which is precisely the behaviour that caused
    the incident. The board is unrewritable history in a public repo, so it gets
    no suppression path.
    """
    text = read_prose(BOARD.name)
    assert text is not None, "Message-board.md is missing or unreadable"

    covered = {f.where() for f in find_findings(BOARD.name, text) if f.digest in suppressed_paths()}
    assert not covered, (
        f"allowlist entries are suppressing {len(covered)} finding(s) in "
        f"{BOARD.name}: {sorted(covered)}. The board is public and its history "
        f"cannot be rewritten, so a credential there is permanent. Remove the "
        f"entry from ALLOWLIST and redact the value instead."
    )


def test_allowlist_entries_are_still_needed() -> None:
    """Every entry still suppresses something.

    Entries rot as the underlying lines are fixed, and a stale entry is a hole
    waiting for a future value that happens to collide. Removing it is the fix.
    """
    findings = scan_prose()
    for digest, expiry, owner, justification in ALLOWLIST:
        sites = [f.where() for f in findings if f.digest == digest]
        assert sites, (
            f"allowlist entry {digest} ({owner}, expires {expiry}) no longer "
            f"suppresses anything. The lines it covered have been fixed. Delete "
            f"the entry -- justification on record was: {justification}"
        )


# --------------------------------------------------------------------------
# Detector self-tests
#
# Without these, "the guard passes" is ambiguous: it could mean nothing is wrong,
# or that the detector matches nothing at all. These pin the behaviour from both
# sides using synthetic strings, so no real credential is ever needed.
# --------------------------------------------------------------------------

_SYNTHETIC_STRONG = [
    "Zq7#mR2vL9!xT4",  # plain mixed literal
    "Corr3ct-H0rse!Battery",  # hyphens are a symbol
    "N0t@ReaL-Passw0rd",  # short-ish but strong
]


@pytest.mark.parametrize("value", _SYNTHETIC_STRONG)
def test_detector_flags_invented_credentials(value: str) -> None:
    """An invented password is caught, so this is a detector and not a lookup.

    These strings are generated, not the value from the incident, and are not
    present in the repo.
    """
    assert is_strong_password_literal(value), f"shape predicate missed {value!r}"
    assert find_findings(
        "synthetic.md", f'password = "{value}"'
    ), f"an invented credential in an assignment was not detected: {value!r}"


def test_detector_catches_every_written_form() -> None:
    """Assignment, escaped JSON, plain JSON and markdown table all fire.

    The incident used three of these four across its four occurrences. A
    detector that only understands one of them would have found at most a
    quarter of it.
    """
    value = _SYNTHETIC_STRONG[0]
    samples = {
        "assignment": f"password = '{value}'",
        "assignment-variable": f"PASS='{value}'",
        "assignment-yaml": f"  password: {value!r}".replace("'", '"'),
        "escaped-json": r"{\"email\":\"a@b.co\",\"password\":\"" + value + r"\"}",
        "json": f'{{"password": "{value}"}}',
        "table-cell": f"| password | `{value}` |",
    }
    for label, line in samples.items():
        found = find_findings("synthetic.md", line)
        assert found, f"form {label!r} not detected in: {line!r}"
        assert found[0].form in _FORMS_BY_LABEL(
            label
        ), f"form {label!r} matched with the wrong matcher: {found[0].form!r}"


@pytest.mark.parametrize(
    "line",
    [
        "API_SECRET='{v}'",
        "DB_PASSWORD='{v}'",
        'APP_SECRET="{v}"',
        "MY_SERVICE_PASSPHRASE='{v}'",
    ],
)
def test_detector_catches_prefixed_env_var_names(line: str) -> None:
    """``API_SECRET=`` must match even though ``\\bSECRET\\b`` does not.

    Regression test, not theory. The first version of this guard anchored the
    label with ``\\b``, which silently skipped every SCREAMING_SNAKE env-var
    name because "_" is a word character, so no word boundary exists before the
    label. Verification against a planted credential missed it; this pins the
    fix. The trailing guard below is the other half: a lowercase run-on word is
    not a label, and must not start matching now that the anchor is loose.
    """
    value = _SYNTHETIC_STRONG[1]
    assert find_findings(
        "synthetic.md", line.format(v=value)
    ), f"credential hidden behind a prefixed variable name was not detected: {line!r}"
    assert not find_findings("synthetic.md", f"mypasswordnotalabel='{value}'"), (
        "a lowercase run-on identifier must not count as a credential label; the "
        "loosened anchor is supposed to match PREFIXED_LABELS, not any word ending in one"
    )


def test_detector_catches_unquoted_env_scalars() -> None:
    """Ops config is written unquoted often enough to be worth covering.

    ``JWT_SECRET: Vq9!nHb3vCy6@Ke`` with no quotes around the value is the one
    shape the quoted forms cannot see.
    """
    value = _SYNTHETIC_STRONG[2]
    found = find_findings("synthetic.md", f"JWT_SECRET: {value}")
    assert found, "an unquoted YAML/env scalar carrying a password was not detected"
    assert found[0].form == "unquoted-scalar"


def test_every_form_exposes_exactly_one_value_group() -> None:
    """Every form must capture the value in group 1, and nothing else.

    Found the hard way while measuring this rule. Appending a bare ``|token`` to
    the label list, without wrapping the new alternatives, puts them *outside*
    the ``(?:...)`` group. The trailing ``(...)`` then binds only to the last
    alternative, and the other alternatives match a bare word with an unset
    group. The symptom is a crash inside ``len()`` far from the cause, or worse,
    a form that quietly matches the wrong thing.
    """
    for form, pattern in _FORMS:
        assert pattern.groups == 1, (
            f"form {form!r} exposes {pattern.groups} capture groups, expected exactly 1. "
            f"Each form must capture only the candidate value, in group 1."
        )
    # And the label itself must stay a single non-capturing group, or the
    # alternation escapes it as described above.
    assert _LABEL.startswith("(?:") and _LABEL.endswith(
        ")"
    ), f"_LABEL must be one non-capturing group, got {_LABEL[:40]!r}...{_LABEL[-20:]!r}"


def _FORMS_BY_LABEL(label: str) -> set[str]:
    return {
        "assignment": {"assignment"},
        "assignment-variable": {"assignment"},
        "assignment-yaml": {"assignment"},
        "escaped-json": {"escaped-json"},
        "json": {"json"},
        "table-cell": {"table-cell"},
    }[label]


@pytest.mark.parametrize(
    "label",
    ["password", "pass", "passwd", "pwd", "passphrase", "secret", "api_key", "auth_token"],
)
def test_detector_is_not_tied_to_the_word_password(label: str) -> None:
    """A credential is a credential whatever the field is called.

    Otherwise renaming the field is a one-character bypass.
    """
    value = _SYNTHETIC_STRONG[1]
    assert find_findings(
        "synthetic.md", f'{label} = "{value}"'
    ), f"label {label!r} was not recognised; the rule must not depend on the word 'password'"


def test_detector_ignores_credential_shaped_placeholders() -> None:
    """Identifier-shaped and placeholder values are not passwords.

    Each of these has appeared in real prose on this repo and none is a secret.
    If any of them started matching, the guard would be unusable.
    """
    non_secrets = [
        "a37cef23-4f8e-4a3c-b35d-e8ca1bce0867",  # uuid
        "ff2d1ceb9a3f4c2e8d5b6a1c0e9f8d7c6b5a4938",  # git sha
        "${DB_PASSWORD}",  # variable reference
        "<your-password-here>",  # angle-bracket placeholder
        "XXXXXXXXXXXXXXXX",  # masked
        "REDACTED_VALUE_HERE",  # upper snake placeholder
        "supersecretpassword",  # long, but no digit and no symbol
        "correct horse battery",  # phrase, not a token
        "not-a-real-password",  # the fixture value chosen for #920
    ]
    for value in non_secrets:
        assert not is_strong_password_literal(value), f"false positive on {value!r}"
        assert not find_findings(
            "synthetic.md", f'password = "{value}"'
        ), f"false positive on {value!r}"


def test_detector_ignores_prose_that_merely_mentions_passwords() -> None:
    """Talking *about* passwords is not disclosing one.

    This is the failure mode that kills naive rules on this repo: the board has
    dozens of lines of genuine security discussion. Sample text, no secrets.
    """
    prose = """
The password is **deliberately not on this board** - this file is committed to a
**public** repo, so a working credential would live in permanent git history.
Per AGENTS.md section 24.

CodeQL says "sensitive data (password) is used in a hashing algorithm (BLAKE2S)
that is insecure for password hashing". No password reaches it.

Invalid email or password. Please try again.

The function accepts any `str`, so nothing stops a future caller passing a
password, a request object, or anything derived from one.

password: the account password, required, 8-128 characters
"""
    assert not find_findings("synthetic.md", prose), (
        "prose discussing passwords must not be reported; the guard would fire on "
        "hundreds of existing lines and get disabled"
    )


def test_scoped_corpus_is_non_trivial() -> None:
    """The guard must actually be looking at the files agents write into.

    Guards that silently enumerate nothing always pass. This fails if the corpus
    ever collapses to zero files, which would happen if the glob or the git call
    broke.
    """
    files = tracked_prose_files()
    assert len(files) >= 50, f"prose corpus collapsed to {len(files)} files; the guard is blind"
    for required in ("Message-board.md", "AGENTS.md"):
        assert required in files, f"{required} is not in the scanned corpus"
    assert any(
        f.startswith("PROJECT_MEMORY/") for f in files
    ), "PROJECT_MEMORY/ is not being scanned"
    assert any(f.startswith("docs/") for f in files), "docs/ is not being scanned"
