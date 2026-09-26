#!/usr/bin/env python3
import pathlib
import re
import subprocess
import sys
from datetime import date

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = list((REPO / ".github" / "workflows").glob("*.yml")) + list((REPO / ".github" / "workflows").glob("*.yaml"))
TRIVY_YAML = REPO / ".trivy.yaml"
TRIVYIGNORE = REPO / ".trivyignore"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(r"uses:\s*([^\s#@]+)@([^\s#]+)")

SENSITIVE_EXTS = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".jks")
SENSITIVE_GRAPH_KEYWORDS = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".jks", "localhost-key", "localhost.pem", "apps/web/certificates")

def check_action_pinning(paths):
    violations = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="ignore")
        for idx, line in enumerate(text.splitlines(), 1):
            m = USES_RE.search(line)
            if not m:
                continue
            action = m.group(1).strip()
            raw_ref = m.group(2).strip()
            ref = raw_ref.split()[0].split("#")[0].strip().strip('"').strip("'")
            if action.startswith("./") or action.startswith("docker://"):
                continue
            if ref.startswith("$"):
                continue
            if not SHA_RE.match(ref):
                try:
                    disp = str(p.relative_to(REPO))
                except ValueError:
                    disp = str(p)
                violations.append(f"{disp}:{idx}: {action}@{raw_ref} is not pinned to 40-char SHA")
    return violations

def parse_inline_skip_list(line):
    parts = []
    m = re.search(r'"([^"]+)"', line)
    if m:
        inner = m.group(1)
        for token in inner.split(","):
            token = token.strip().strip('"').strip("'")
            if token:
                parts.append(token)
        return parts
    m = re.search(r"'([^']+)'", line)
    if m:
        inner = m.group(1)
        for token in inner.split(","):
            token = token.strip()
            if token:
                parts.append(token)
        return parts
    return []

def _disp(p):
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)

def collect_skip_patterns():
    patterns = []
    sources = []
    for p in WORKFLOWS:
        text = p.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        for idx, line in enumerate(lines, 1):
            low = line.lower()
            if "skip-dirs" in low or "skip-files" in low:
                sources.append(f"{_disp(p)}:{idx}: {line.strip()}")
                for tok in parse_inline_skip_list(line):
                    patterns.append((_disp(p), idx, tok))
                if ":" in line and '"' not in line and "'" not in line:
                    after = line.split(":", 1)[1].strip()
                    if after and not after.startswith('"'):
                        for tok in after.split(","):
                            tok = tok.strip().strip('"').strip("'")
                            if tok and tok not in ("skip-dirs", "skip-files"):
                                patterns.append((_disp(p), idx, tok))
    trivy = TRIVY_YAML
    # allow override for tests
    if isinstance(trivy, pathlib.Path) and trivy.exists():
        text = trivy.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        in_skip = None
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            low = stripped.lower()
            if low.startswith("skip-dirs:") or low.startswith("skip_dirs:"):
                in_skip = "dirs"
                sources.append(f"{_disp(trivy)}:{idx}: {stripped}")
                continue
            if low.startswith("skip-files:") or low.startswith("skip_files:"):
                in_skip = "files"
                sources.append(f"{_disp(trivy)}:{idx}: {stripped}")
                continue
            if in_skip:
                if stripped.startswith("-"):
                    val = stripped.lstrip("-").strip().strip('"').strip("'").strip()
                    if val:
                        patterns.append((_disp(trivy), idx, val))
                    continue
                elif stripped and not stripped.startswith("#") and ":" in stripped:
                    in_skip = None
                elif not stripped:
                    continue
                else:
                    if stripped:
                        in_skip = None
    return patterns, sources

def check_suppressions():
    violations = []
    patterns, sources = collect_skip_patterns()
    for src, idx, pat in patterns:
        low = pat.lower()
        is_sensitive = False
        if low.endswith(SENSITIVE_EXTS):
            is_sensitive = True
        elif ".pem" in low or ".key" in low or ".p12" in low or ".pfx" in low or ".crt" in low or ".cer" in low:
            if "*" in low or "/" in low:
                is_sensitive = True
        elif "localhost-key" in low or "localhost.pem" in low:
            is_sensitive = True
        elif "apps/web/certificates" in low:
            is_sensitive = True
        elif low in (".pem", ".key"):
            is_sensitive = True
        if is_sensitive:
            violations.append(f"{src}:{idx}: suppression pattern '{pat}' suppresses sensitive private-key/certificate artifact")
    committed = get_committed_sensitive_files()
    if committed:
        for src, idx, pat in patterns:
            for f in committed:
                norm_pat = pat.replace("*", "")
                if norm_pat and norm_pat in f:
                    violations.append(f"{src}:{idx}: committed sensitive file '{f}' is suppressed by pattern '{pat}'")
        if committed and not any(v.startswith(tuple(patterns[0][0] if patterns else "")) for v in violations):
            for f in committed:
                if f not in [p for _, _, p in patterns]:
                    pass
    return violations, sources, patterns

def get_committed_sensitive_files():
    try:
        result = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return []
        files = result.stdout.splitlines()
    except Exception:
        return []
    sensitive = []
    for f in files:
        low = f.lower()
        if low.endswith(SENSITIVE_EXTS):
            sensitive.append(f)
        elif "localhost-key.pem" in low or "localhost.pem" in low:
            sensitive.append(f)
    return sensitive

def check_trivyignore():
    violations = []
    active = []
    if not TRIVYIGNORE.exists():
        return violations, active
    text = TRIVYIGNORE.read_text(encoding="utf-8", errors="ignore")
    for idx, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        active.append(f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: {stripped}")
        parts = [p.strip() for p in stripped.split(",", 3)]
        if len(parts) != 4:
            violations.append(
                f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: suppression '{stripped}' "
                "must use CVE, expiry (YYYY-MM-DD), owner (@handle), justification"
            )
            continue

        cve, expiry_text, owner, justification = parts
        if "*" in cve:
            violations.append(
                f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: wildcard CVE suppression is not allowed"
            )
        try:
            expiry = date.fromisoformat(expiry_text)
        except ValueError:
            violations.append(
                f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: invalid expiry date '{expiry_text}'"
            )
        else:
            if expiry <= date.today():
                violations.append(
                    f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: suppression expired on {expiry_text}"
                )
        if not re.fullmatch(r"@[A-Za-z0-9_.-]+", owner):
            violations.append(
                f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: suppression owner must be an @handle"
            )
        if not justification:
            violations.append(
                f"{TRIVYIGNORE.relative_to(REPO)}:{idx}: suppression justification is required"
            )
    return violations, active

def report_active_suppressions():
    lines = []
    lines.append("Active scanner suppressions:")
    _, sources = collect_skip_patterns()
    if sources:
        lines.append("  skip-dirs/skip-files sources:")
        for s in sources:
            lines.append(f"    {s}")
    else:
        lines.append("  no skip-dirs/skip-files found")
    patterns, _ = collect_skip_patterns()
    if patterns:
        lines.append("  parsed skip patterns:")
        for src, idx, pat in patterns:
            lines.append(f"    {src}:{idx} -> {pat}")
    if TRIVYIGNORE.exists():
        text = TRIVYIGNORE.read_text(encoding="utf-8", errors="ignore")
        lines.append("  .trivyignore entries:")
        found = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(f"    {stripped}")
            found = True
        if not found:
            lines.append("    (none)")
    else:
        lines.append("  no .trivyignore")
    if TRIVY_YAML.exists():
        lines.append(f"  {TRIVY_YAML.relative_to(REPO)} exists")
    for p in WORKFLOWS:
        lines.append(f"  workflow: {p.relative_to(REPO)}")
    return "\n".join(lines)

def main():
    ok = True
    out = []
    out.append("Supply chain guard #809")
    pin_violations = check_action_pinning(WORKFLOWS)
    if pin_violations:
        ok = False
        out.append("FAIL: mutable action references (must be 40-char SHA):")
        for v in pin_violations:
            out.append(f"  {v}")
    else:
        out.append("PASS: all third-party actions pinned to 40-char SHA")
    supp_violations, sources, patterns = check_suppressions()
    if supp_violations:
        ok = False
        out.append("FAIL: scanner suppression violates private-key policy:")
        for v in supp_violations:
            out.append(f"  {v}")
    else:
        out.append("PASS: no suppression hides committed private key / sensitive artifact")
    trivy_violations, active = check_trivyignore()
    if trivy_violations:
        ok = False
        out.append("FAIL: .trivyignore entries missing justification/owner/expiry:")
        for v in trivy_violations:
            out.append(f"  {v}")
    else:
        if active:
            out.append(f"PASS: .trivyignore entries have justification/owner/expiry ({len(active)} active)")
        else:
            out.append("PASS: no .trivyignore active suppressions")
    committed = get_committed_sensitive_files()
    if committed:
        ok = False
        out.append("FAIL: committed private-key/certificate artifacts found (not allowlisted):")
        for f in committed:
            out.append(f"  {f}")
    else:
        out.append("PASS: no committed private-key/certificate artifacts")
    out.append("")
    out.append(report_active_suppressions())
    print("\n".join(out))
    if not ok:
        print("\nGuard FAILED", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nGuard PASSED", file=sys.stderr)
        sys.exit(0)

if __name__ == "__main__":
    main()
