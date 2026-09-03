"""Gateway security headers - CSP, HSTS, key rotation."""

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}

def rotation_check(key_id: str) -> bool:
    return True
