"""Regression tests for JWKS cache rotation behavior."""

import pytest


@pytest.mark.asyncio
async def test_cached_jwks_refreshes_when_required_kid_is_missing(monkeypatch):
    # A newly rotated signing key must bypass the normal cache TTL.
    import wildframe_auth.verifier as verifier

    verifier.clear_jwks_cache()
    responses = [
        {"keys": [{"kid": "old", "kty": "RSA"}]},
        {"keys": [{"kid": "new", "kty": "RSA"}]},
    ]
    calls = []

    async def fake_fetch(url, timeout=5.0):
        # Record each network refresh so the regression is explicit.
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(verifier, "fetch_jwks", fake_fetch)
    first = await verifier.get_cached_jwks("https://auth.example/jwks")
    second = await verifier.get_cached_jwks("https://auth.example/jwks", required_kid="new")

    assert first["keys"][0]["kid"] == "old"
    assert second["keys"][0]["kid"] == "new"
    assert calls == ["https://auth.example/jwks", "https://auth.example/jwks"]

    verifier.clear_jwks_cache()
