"""Resolve a platform principal from FastMCP's verified access-token context.

The client_id is configured server-side in StaticTokenVerifier's token registry.
Never accept a principal ID from tool arguments or request headers. This helper
is a boundary for later integration and is not yet wired into account routing.
"""
from __future__ import annotations

from fastmcp.server.dependencies import get_access_token


class AuthenticatedIdentityError(PermissionError):
    """The current request has no usable trusted platform principal."""


def require_authenticated_principal_id() -> str:
    """Return the verified token's configured client_id, failing closed otherwise."""
    try:
        token = get_access_token()
    except Exception as exc:
        raise AuthenticatedIdentityError("Authenticated principal unavailable") from exc

    if token is None:
        raise AuthenticatedIdentityError("Authenticated principal unavailable")

    # StaticTokenVerifier maps a presented token to this server-configured
    # client_id. Do not use raw bearer token, tool arguments, or arbitrary claims
    # as an account ownership principal.
    principal_id = getattr(token, "client_id", None)
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise AuthenticatedIdentityError("Authenticated principal unavailable")

    return principal_id.strip()


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
