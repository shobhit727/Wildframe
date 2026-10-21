"""Shared helpers for the contract suite.

The dev-certificate helper lives here rather than in one of the test modules
because two of them need it: the module that owns the SAN-list behaviour, and
the module that owns the "the script produces a valid PEM pair" behaviour.
Duplicating the sandbox setup in both would let the two drift, and the second
copy would be the one nobody re-reads.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: The address that the old script hardcoded into the certificate. It is one
#: developer's machine, so it is wrong everywhere else -- and it is asserted
#: *absent* below, which is the only reason it is still named here.
OLD_HARDCODED_LAN_ADDRESS = "192.168.1.14"

#: An address the host running these tests cannot actually have. The fakes
#: report it, so the expected SAN list is independent of the machine.
FAKE_IPV4 = "10.11.12.13"
FAKE_IPV6 = "2001:db8:85a3::8a2e:370:7334"

#: Emitted in the format `ip -o addr show scope global` produces, so the
#: script's own parser is exercised rather than bypassed.
_FAKE_IP_LINES = (
    "4: test0    inet 10.11.12.13/24 brd 10.11.12.255 scope global test0",
    "4: test0    inet6 2001:db8:85a3::8a2e:370:7334/64 scope global",
)

#: Emitted in the format `ifconfig` produces on macOS and the BSDs. The
#: link-local address carries a zone index and must not reach the certificate.
_FAKE_IFCONFIG_OUTPUT = (
    "en0: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500\n"
    "\tinet 10.11.12.13 netmask 0xffffff00 broadcast 10.11.12.255\n"
    "\tinet6 fe80::1%en0 prefixlen 64 scopeid 0x5\n"
    "\tinet6 2001:db8:85a3::8a2e:370:7334 prefixlen 64 autoconf\n"
    "lo0: flags=8049<UP,LOOPBACK,RUNNING> mtu 16384\n"
    "\tinet 127.0.0.1 netmask 0xff000000\n"
    "\tinet6 ::1 prefixlen 128\n"
)

#: `hostname -I` prints the addresses space-separated on a single line.
_FAKE_HOSTNAME_OUTPUT = "10.11.12.13 2001:db8:85a3::8a2e:370:7334"

#: A tool that exists but reports nothing.
_EMPTY_TOOL = "#!/usr/bin/env bash\nexit 0\n"


def _fake_ip_command() -> str:
    """A fake ``ip`` that reports addresses the host cannot actually have."""
    quoted = " \\\n  ".join(f"'{line}'" for line in _FAKE_IP_LINES)
    return (
        "#!/usr/bin/env bash\n"
        "# Fake iproute2 for the contract suite.\n"
        f"printf '%s\\n' \\\n  {quoted}\n"
    )


def _fake_ifconfig_command() -> str:
    quoted = _FAKE_IFCONFIG_OUTPUT.replace("\n", "\\n")
    return (
        "#!/usr/bin/env bash\n"
        "# Fake ifconfig for the contract suite.\n"
        f"printf '%b' '{quoted}'\n"
    )


def _fake_hostname_command() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "# Fake hostname for the contract suite.\n"
        'if [ "$1" = "-I" ]; then\n'
        f"  printf '%s\\n' '{_FAKE_HOSTNAME_OUTPUT}'\n"
        "  exit 0\n"
        "fi\n"
        'exec /usr/bin/hostname "$@"\n'
    )


def _parse_san_entries(cert: Path) -> list[str]:
    """Return the subjectAltName entries of ``cert`` as raw strings.

    ``openssl x509 -ext subjectAltName`` renders an IPv6 address in its
    expanded form (``2001:DB8:85A3:0:0:8A2E:370:7334``), so the raw strings
    are not comparable with what the script passed in. Use
    :attr:`GeneratedCert.ip_addresses` to compare addresses.
    """
    result = subprocess.run(
        ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"openssl could not read {cert}: {result.stderr}"
    body = result.stdout.split(":", 1)[-1]
    entries = []
    for raw in body.split(","):
        raw = raw.strip()
        if not raw:
            continue
        entries.append(raw.split(":", 1)[1].strip() if ":" in raw else raw)
    return entries


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class GeneratedCert:
    """A certificate produced by a sandboxed run of the script."""

    path: Path
    key_path: Path
    ip_addresses: frozenset[str]
    dns_names: frozenset[str]

    @property
    def old_hardcoded_address(self) -> str:
        """The address the script used to hardcode, for negative assertions."""
        return OLD_HARDCODED_LAN_ADDRESS

    @property
    def fake_ipv4(self) -> str:
        """The IPv4 address the fake tools report."""
        return FAKE_IPV4

    @property
    def fake_ipv6(self) -> str:
        """The IPv6 address the fake tools report."""
        return FAKE_IPV6


@pytest.fixture
def generate_dev_certs(tmp_path: Path):
    """Run the real certificate script in a sandbox and return its certificate.

    The script derives its output directory from its own location, so it is
    copied into a throwaway tree: running it in the checkout would overwrite
    the developer's own certificates, and the test suite must never do that.

    ``address_tool`` selects which tool the script is allowed to find, so the
    platform fallbacks can be exercised on a machine that has all of them:

    ``"ip"``
        ``ip`` reports the addresses (the Linux path).
    ``"ifconfig"``
        ``ip`` exists but reports nothing, ``ifconfig`` reports them (macOS).
    ``"hostname"``
        ``ip`` and ``ifconfig`` report nothing, ``hostname -I`` does (minimal
        Linux).
    ``"empty"``
        Every tool reports nothing, so only the loopback SANs remain.
    ``"real"``
        No fakes at all; the machine's own tools are used.
    """
    sandbox = tmp_path / "sandbox"
    (sandbox / "scripts").mkdir(parents=True)
    script = sandbox / "scripts" / "generate-dev-certs.sh"
    shutil.copy2(REPO / "scripts" / "generate-dev-certs.sh", script)

    def _run(*, address_tool: str = "ip", extra_ips: str | None = None) -> GeneratedCert:
        env = dict(os.environ)
        reporting = {
            "ip": _fake_ip_command(),
            "ifconfig": _fake_ifconfig_command(),
            "hostname": _fake_hostname_command(),
        }
        order = ["ip", "ifconfig", "hostname"]
        tools: list[tuple[str, str]] = []
        if address_tool in reporting:
            chosen = order.index(address_tool)
            # Everything the script tries before the chosen tool is shadowed
            # with a tool that exists but says nothing, so the script has to
            # fall through to the one under test.
            for name in order[:chosen]:
                tools.append((name, _EMPTY_TOOL))
            tools.append((address_tool, reporting[address_tool]))
            for name in order[chosen + 1 :]:
                tools.append((name, _EMPTY_TOOL))
        elif address_tool == "empty":
            tools = [(name, _EMPTY_TOOL) for name in order]
        elif address_tool != "real":
            raise ValueError(f"unknown address_tool: {address_tool!r}")

        if tools:
            bindir = sandbox / "bin"
            bindir.mkdir(exist_ok=True)
            for name, body in tools:
                tool = bindir / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        if extra_ips is not None:
            env["DEV_CERT_EXTRA_IPS"] = extra_ips

        result = subprocess.run(
            ["bash", str(script)],
            env=env,
            cwd=sandbox,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"generate-dev-certs.sh failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
        cert_dir = sandbox / "apps" / "web" / "certificates"
        cert = cert_dir / "localhost.pem"
        assert cert.exists(), f"the script did not produce {cert}"

        entries = _parse_san_entries(cert)
        return GeneratedCert(
            path=cert,
            key_path=cert_dir / "localhost-key.pem",
            ip_addresses=frozenset(str(ipaddress.ip_address(e)) for e in entries if _is_ip(e)),
            dns_names=frozenset(e for e in entries if e and not _is_ip(e)),
        )

    return _run
