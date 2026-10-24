"""Compose verified client identity, explicit principal mapping, and account policy.

This is an internal integration seam. It is not wired into existing tools; callers
must invoke it before touching account-scoped data or side effects.
"""
from __future__ import annotations

from collections.abc import Mapping

from account_registry import AccountRegistry
from auth_identity import require_authenticated_principal_id
from principal_mapping import resolve_principal_id


def require_current_request_account_access(
    registry: AccountRegistry,
    account_id: str,
    action: str,
    client_to_principal: Mapping[str, str],
) -> str:
    """Authorize the verified client via explicit mapping and return principal ID.

    A token client_id is not itself proof of account ownership. The mapping must
    be supplied by trusted server configuration, never by tool arguments.
    """
    verified_client_id = require_authenticated_principal_id()
    principal_id = resolve_principal_id(verified_client_id, client_to_principal)
    registry.require_account_access(principal_id, account_id, action)
    return principal_id
