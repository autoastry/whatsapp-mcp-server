"""Explicit server-side mapping from verified token client IDs to principals.

This module is intentionally not wired into request authorization yet. It
provides a strict, testable policy seam: unknown clients never become principals
by fallback, and no caller-supplied value is accepted as mapping configuration.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class PrincipalMappingError(ValueError):
    """Raised when principal mapping configuration is missing or ambiguous."""


def parse_principal_mapping(raw: str) -> dict[str, str]:
    """Parse JSON object {client_id: principal_id}, rejecting unsafe ambiguity."""
    if not isinstance(raw, str) or not raw.strip():
        raise PrincipalMappingError("Principal mapping configuration is required")
    try:
        value: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PrincipalMappingError("Principal mapping must be valid JSON") from exc
    if not isinstance(value, dict) or not value:
        raise PrincipalMappingError("Principal mapping must be a non-empty JSON object")

    mapping: dict[str, str] = {}
    principals: set[str] = set()
    for client_id, principal_id in value.items():
        if not isinstance(client_id, str) or not client_id.strip():
            raise PrincipalMappingError("Client IDs must be non-empty strings")
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise PrincipalMappingError("Principal IDs must be non-empty strings")
        client_id = client_id.strip()
        principal_id = principal_id.strip()
        if client_id in mapping:
            raise PrincipalMappingError("Duplicate client ID")
        if principal_id in principals:
            raise PrincipalMappingError("Each client must map to a unique principal")
        mapping[client_id] = principal_id
        principals.add(principal_id)
    return mapping


def resolve_principal_id(
    verified_client_id: str | None, mapping: Mapping[str, str]
) -> str:
    """Resolve only a verified client ID through an explicit mapping; fail closed."""
    if not isinstance(verified_client_id, str) or not verified_client_id.strip():
        raise PrincipalMappingError("Authenticated principal unavailable")
    client_id = verified_client_id.strip()
    principal_id = mapping.get(client_id)
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise PrincipalMappingError("Authenticated principal unavailable")
    return principal_id.strip()
