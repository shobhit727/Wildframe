"""Gateway response security headers - CSP, HSTS, and MIME/frame protections.

Signing-key rotation is *not* handled here. The gateway holds no key set and
no rotation schedule; it delegates to the shared ``wildframe_auth`` verifier,
which caches JWKS by TTL and refreshes once on an unknown ``kid`` (see
``packages/sdk/wildframe_auth/verifier.py``). This module covers the
static response headers only.
"""

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}
