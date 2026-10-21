"""The dev certificate's SAN list must follow the host, not a constant.

``scripts/generate-dev-certs.sh`` used to hardcode one developer's LAN address
into the certificate's ``subjectAltName``. That address is wrong on every other
machine, and it was incomplete on the machine it was written on too: a host
with two IPv4 addresses had only one of them covered, so a browser connecting
through the other one failed hostname verification -- and the failure presented
as a broken stack rather than as a wrong certificate.

The tests here drive the real script with a fake ``ip`` on ``PATH`` that
reports an address the host cannot have. That is what gives them the ability
to fail: against the old script the certificate carried the hardcoded address
and not the injected one, so these assertions went red rather than passing
for a reason unrelated to the behaviour under test.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "generate-dev-certs.sh"

#: A private-range IPv4 address as a literal. Loopback and the unspecified
#: address are excluded because they are legitimate constant entries.
_LAN_IPV4 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)


def test_script_source_has_no_hardcoded_lan_address() -> None:
    """No developer's address may be baked into the committed script.

    This is the guard that stops the defect coming back. It scans the whole
    file, comments included, so a literal cannot survive in a comment either.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    found = sorted(set(_LAN_IPV4.findall(source)))
    assert not found, (
        f"{SCRIPT} contains hardcoded private-range address(es) {found}. "
        "The certificate's SAN list is derived from the host's interfaces at "
        "run time; a literal address is wrong on every other machine. Use "
        "DEV_CERT_EXTRA_IPS for an address detection cannot see."
    )


def test_certificate_sans_follow_the_hosts_interfaces(generate_dev_certs) -> None:
    """The certificate must cover the addresses the host reports.

    The fake ``ip`` reports one IPv4 and one IPv6 address. Both must appear in
    the certificate, and the previously hardcoded address must not.
    """
    cert = generate_dev_certs(address_tool="ip")

    assert cert.fake_ipv4 in cert.ip_addresses, (
        f"the certificate does not cover the host's IPv4 address "
        f"{cert.fake_ipv4}; it covers {sorted(cert.ip_addresses)}"
    )
    assert cert.fake_ipv6 in cert.ip_addresses, (
        f"the certificate does not cover the host's IPv6 address "
        f"{cert.fake_ipv6}; it covers {sorted(cert.ip_addresses)}"
    )
    assert cert.old_hardcoded_address not in cert.ip_addresses, (
        "the certificate still carries the previously hardcoded address; the "
        "SAN list is supposed to be derived from the host, not from a literal"
    )


def test_ifconfig_fallback_covers_the_host_addresses(generate_dev_certs) -> None:
    """macOS has no ``ip``, so the ``ifconfig`` path must carry the load.

    The fake ``ifconfig`` reports a link-local address with a zone index as
    well as the global ones. The link-local address must be dropped: it is
    useless for a dev server, and a zone index is not valid in a SAN.
    """
    cert = generate_dev_certs(address_tool="ifconfig")

    assert cert.fake_ipv4 in cert.ip_addresses, (
        f"the ifconfig path lost the host's IPv4 address; " f"it covers {sorted(cert.ip_addresses)}"
    )
    assert cert.fake_ipv6 in cert.ip_addresses, (
        f"the ifconfig path lost the host's IPv6 address; " f"it covers {sorted(cert.ip_addresses)}"
    )
    assert not any(
        a.startswith("fe80:") for a in cert.ip_addresses
    ), f"a link-local address reached the certificate: {sorted(cert.ip_addresses)}"


def test_hostname_fallback_covers_the_host_addresses(generate_dev_certs) -> None:
    """Minimal Linux has neither ``ip`` nor ``ifconfig``, only ``hostname -I``.

    ``hostname -I`` prints every address space-separated on one line, so a
    parser that expects one address per line reads the whole string as a
    single malformed address and the certificate fails to generate.
    """
    cert = generate_dev_certs(address_tool="hostname")

    assert cert.fake_ipv4 in cert.ip_addresses, (
        f"the hostname fallback lost the host's IPv4 address; "
        f"it covers {sorted(cert.ip_addresses)}"
    )
    assert cert.fake_ipv6 in cert.ip_addresses, (
        f"the hostname fallback lost the host's IPv6 address; "
        f"it covers {sorted(cert.ip_addresses)}"
    )


def test_loopback_sans_are_always_present(generate_dev_certs) -> None:
    """Loopback must be covered even when detection finds nothing.

    A host with no global-scope address -- a locked-down CI container, or a
    machine with only a loopback interface -- must still get a certificate the
    local dev stack can use.
    """
    cert = generate_dev_certs(address_tool="empty")

    assert (
        "127.0.0.1" in cert.ip_addresses
    ), f"loopback IPv4 missing; covered {sorted(cert.ip_addresses)}"
    assert "::1" in cert.ip_addresses, f"loopback IPv6 missing; covered {sorted(cert.ip_addresses)}"
    assert "localhost" in cert.dns_names, "DNS:localhost missing from the certificate"
    assert (
        cert.fake_ipv4 not in cert.ip_addresses
    ), "an address appeared even though every tool reported nothing"


def test_extra_ips_env_var_extends_the_san_list(generate_dev_certs) -> None:
    """DEV_CERT_EXTRA_IPS adds an address that detection cannot see.

    A VPN alias or a second NIC reported unusually by the platform tool is not
    always discoverable, so the script offers an explicit override.
    """
    cert = generate_dev_certs(address_tool="ip", extra_ips="203.0.113.7,2001:db8::99")

    assert (
        "203.0.113.7" in cert.ip_addresses
    ), f"DEV_CERT_EXTRA_IPS IPv4 missing; covered {sorted(cert.ip_addresses)}"
    assert (
        "2001:db8::99" in cert.ip_addresses
    ), f"DEV_CERT_EXTRA_IPS IPv6 missing; covered {sorted(cert.ip_addresses)}"
    # The override adds to the list; it does not replace it.
    assert cert.fake_ipv4 in cert.ip_addresses, "DEV_CERT_EXTRA_IPS replaced the detected addresses"


def test_script_explains_how_to_regenerate_after_a_network_change() -> None:
    """The script must say how to pick up a new address.

    The script is idempotent and will not overwrite an existing pair, so a
    developer who moves networks has to know to delete the certificates.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Delete them to regenerate" in source, (
        "the script does not tell the developer how to regenerate the "
        "certificate after moving to a different network"
    )
