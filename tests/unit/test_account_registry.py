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
    registry.set_account_status("alice", account, "revoked")
    with pytest.raises(AuthorizationDenied):
        registry.require_account_access("alice", account, "read")


def test_unknown_principal_cannot_create_account(registry):
    with pytest.raises(AuthorizationDenied):
        registry.create_account("missing", "not-created")


@pytest.mark.parametrize(
    "status,allowed,denied",
    [
        ("pending_pairing", ("manage",), ("read", "send")),
        ("active", ("read", "send", "manage"), ()),
        ("disconnected", ("read", "manage"), ("send",)),
        ("revoked", (), ("read", "send", "manage")),
        ("error", ("manage",), ("read", "send")),
    ],
)
def test_account_lifecycle_state_gates_actions(registry, status, allowed, denied):
    registry.create_principal("alice")
    account = registry.create_account("alice", f"account-{status}", status)
    for action in allowed:
        registry.require_account_access("alice", account, action)
    for action in denied:
        with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
            registry.require_account_access("alice", account, action)


def test_registry_grants_survive_close_and_reopen(tmp_path):
    db_path = tmp_path / "persistent-platform.db"
    first = AccountRegistry(db_path)
    first.create_principal("alice")
    first.create_principal("bob")
    account = first.create_account("alice", "account-persistent", "active")
    first.grant_access("alice", "bob", account, "operator")
    first.close()

    reopened = AccountRegistry(db_path)
    try:
        reopened.require_account_access("alice", account, "manage")
        reopened.require_account_access("bob", account, "read")
        reopened.require_account_access("bob", account, "send")
        with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
            reopened.require_account_access("bob", account, "manage")
    finally:
        reopened.close()


def test_unknown_action_is_denied(registry):
    account = pair(registry)
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("alice", account, "admin")

def test_registry_refuses_to_initialize_in_database_with_unrelated_tables(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy-store.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE legacy_messages (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="contains unrelated tables"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"legacy_messages"}


def test_registry_rejects_future_schema_without_creating_tables(tmp_path):
    import sqlite3

    db_path = tmp_path / "future-registry.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO schema_migrations(version) VALUES (999)")

    with pytest.raises(RuntimeError, match="newer than this application supports"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"schema_migrations"}

def test_duplicate_account_creation_rolls_back_without_granting_foreign_access(registry):
    import sqlite3

    registry.create_principal("alice")
    registry.create_principal("bob")
    account = registry.create_account("alice", "shared-id", "active")

    with pytest.raises(sqlite3.IntegrityError):
        registry.create_account("bob", "shared-id", "active")

    registry.require_account_access("alice", account, "manage")
    with pytest.raises(AuthorizationDenied):
        registry.require_account_access("bob", account, "read")

    bob_account = registry.create_account("bob", "bob-account", "active")
    registry.require_account_access("bob", bob_account, "manage")



def test_only_owner_can_change_account_lifecycle_state(registry):
    account = pair(registry)

    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.set_account_status("bob", account, "disconnected")

    registry.require_account_access("alice", account, "manage")
    registry.set_account_status("alice", account, "disconnected")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("alice", account, "send")
    registry.require_account_access("alice", account, "manage")
