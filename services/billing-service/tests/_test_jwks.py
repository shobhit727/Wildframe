import base64
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def _b64(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _priv.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
_pub = _priv.public_key()
_nums = _pub.public_numbers()
JWK = {
    "kty": "RSA",
    "kid": "k1",
    "use": "sig",
    "alg": "RS256",
    "n": _b64(_nums.n),
    "e": _b64(_nums.e),
}
JWKS = {"keys": [JWK]}
