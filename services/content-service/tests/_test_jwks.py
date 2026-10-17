"""A real RSA-2048 keypair and the JWKS auth-service publishes for it.

Generated at import time, in memory, so no private key is ever committed
(``tests/contract/test_no_committed_private_key.py`` enforces that). The
signing key is never written to disk: the PEM stays a module-level string that
only the test process holds, and the matching public halves form ``JWKS``.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_numbers = _private_key.public_key().public_numbers()
JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "k1",
            "use": "sig",
            "alg": "RS256",
            "n": _b64(_numbers.n),
            "e": _b64(_numbers.e),
        }
    ]
}
