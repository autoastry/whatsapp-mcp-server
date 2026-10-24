"""Compose trusted request identity with the isolated account access policy.

This is an internal integration seam. It does not itself register middleware or
authorize any existing tool; callers must explicitly invoke it before touching
account-scoped data or side effects.
"""
from __future__ import annotations

from account_registry import AccountRegistry
from auth_identity import require_authenticated_principal_id


def require_current_request_account_access(
    registry: AccountRegistry, account_id: str, action: str
) -> str:
    """Authorize the verified caller for an account and return its principal ID."""
    principal_id = require_authenticated_principal_id()
    registry.require_account_access(principal_id, account_id, action)
    return principal_id
