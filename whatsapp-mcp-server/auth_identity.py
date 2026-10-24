"""Resolve the verified client identifier from FastMCP request auth context.

A client_id identifies a configured token client, not necessarily a platform
principal. Resolve it through an explicit server-owned mapping before using it
for account authorization. Never accept identity from tool arguments or headers.
"""
from __future__ import annotations

from fastmcp.server.dependencies import get_access_token


class AuthenticatedIdentityError(PermissionError):
    """The current request has no usable trusted platform principal."""


def require_verified_client_id() -> str:
    """Return the verified token client_id; this is not a platform principal."""
    try:
        token = get_access_token()
    except Exception as exc:
        raise AuthenticatedIdentityError("Authenticated principal unavailable") from exc

    if token is None:
        raise AuthenticatedIdentityError("Authenticated principal unavailable")

    # StaticTokenVerifier maps a presented token to this server-configured
    # client_id. Do not use raw bearer token, tool arguments, or arbitrary claims
    # as an account ownership principal.
    client_id = getattr(token, "client_id", None)
    if not isinstance(client_id, str) or not client_id.strip():
        raise AuthenticatedIdentityError("Authenticated principal unavailable")

    return client_id.strip()


def audit_client_id_or_unknown() -> str:
    """Return verified token client_id for audit attribution, never a guessed owner.

    Audit logging must remain best-effort even outside a request context. The
    sentinel "unknown" is intentionally not a platform principal and must not be
    used for authorization decisions.
    """
    try:
        token = get_access_token()
    except Exception:
        return "unknown"
    if token is None:
        return "unknown"
    client_id = getattr(token, "client_id", None)
    if not isinstance(client_id, str) or not client_id.strip():
        return "unknown"
    return client_id.strip()
