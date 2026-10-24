from __future__ import annotations

import pytest

from account_registry import AccountRegistry, AuthorizationDenied


@pytest.fixture()
def registry(tmp_path):
    value = AccountRegistry(tmp_path / "platform.db")
    yield value
    value.close()


def pair(registry):
    registry.create_principal("alice")
    registry.create_principal("bob")
    account = registry.create_account("alice", "account-a", "active")
    return account


def test_owner_access_and_foreign_account_denial(registry):
    account = pair(registry)
    registry.require_account_access("alice", account, "read")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("bob", account, "read")


def test_only_owner_can_grant_and_revoke(registry):
    account = pair(registry)
    registry.grant_access("alice", "bob", account, "reader")
    registry.require_account_access("bob", account, "read")
    with pytest.raises(AuthorizationDenied):
        registry.grant_access("bob", "alice", account, "reader")
    registry.revoke_access("alice", "bob", account)
    with pytest.raises(AuthorizationDenied):
        registry.require_account_access("bob", account, "read")


def test_reader_cannot_send_or_manage(registry):
    account = pair(registry)
    registry.grant_access("alice", "bob", account, "reader")
    for action in ("send", "manage"):
        with pytest.raises(AuthorizationDenied):
            registry.require_account_access("bob", account, action)


def test_disabled_principal_and_revoked_account_are_denied(registry):
    account = pair(registry)
    registry.set_principal_status("alice", "disabled")
    with pytest.raises(AuthorizationDenied):
        registry.require_account_access("alice", account, "read")
    registry.set_principal_status("alice", "active")
    registry.set_account_status(account, "revoked")
    with pytest.raises(AuthorizationDenied):
        registry.require_account_access("alice", account, "read")


def test_unknown_principal_cannot_create_account(registry):
    with pytest.raises(AuthorizationDenied):
        registry.create_account("missing", "not-created")
