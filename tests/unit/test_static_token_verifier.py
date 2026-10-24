from __future__ import annotations

import asyncio

from fastmcp.server.auth import StaticTokenVerifier

import auth_identity


def test_locked_fastmcp_static_verifier_returns_server_configured_client_id(monkeypatch):
    """Exercise FastMCP 4.0.3's real verifier, not a fabricated token object."""
    verifier = StaticTokenVerifier(
        tokens={
            "test-token-a": {
                "client_id": "client-a",
                "scopes": ["whatsapp:authenticated", "whatsapp:read"],
            },
        },
        required_scopes=["whatsapp:authenticated"],
    )

    access_token = asyncio.run(verifier.verify_token("test-token-a"))

    assert access_token is not None
    assert access_token.client_id == "client-a"
    assert "whatsapp:read" in access_token.scopes

    # The identity helper receives the verifier-produced token shape used by
    # FastMCP's request context. This is not an end-to-end HTTP transport test.
    monkeypatch.setattr(auth_identity, "get_access_token", lambda: access_token)
    assert auth_identity.require_verified_client_id() == "client-a"


def test_locked_fastmcp_static_verifier_rejects_unknown_token():
    verifier = StaticTokenVerifier(
        tokens={
            "known-token": {
                "client_id": "client-a",
                "scopes": ["whatsapp:authenticated"],
            },
        },
        required_scopes=["whatsapp:authenticated"],
    )

    assert asyncio.run(verifier.verify_token("unknown-token")) is None


def test_locked_fastmcp_static_verifier_rejects_missing_required_scope():
    verifier = StaticTokenVerifier(
        tokens={
            "read-only-token": {
                "client_id": "client-a",
                "scopes": ["whatsapp:read"],
            },
        },
        required_scopes=["whatsapp:authenticated"],
    )

    assert asyncio.run(verifier.verify_token("read-only-token")) is None
