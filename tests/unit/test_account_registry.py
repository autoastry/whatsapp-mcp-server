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


def test_disabled_principal_cannot_receive_account_grant(registry):
    account = pair(registry)
    registry.set_principal_status("bob", "disabled")

    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.grant_access("alice", "bob", account, "reader")

    registry.set_principal_status("bob", "active")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("bob", account, "read")


def test_owner_can_restore_revoked_non_owner_grant_with_explicit_grant(registry):
    account = pair(registry)
    registry.grant_access("alice", "bob", account, "reader")
    registry.revoke_access("alice", "bob", account)

    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("bob", account, "read")

    registry.grant_access("alice", "bob", account, "operator")
    registry.require_account_access("bob", account, "read")
    registry.require_account_access("bob", account, "send")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("bob", account, "manage")


def test_grant_cannot_downgrade_existing_account_owner(registry):
    account = pair(registry)

    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.grant_access("alice", "alice", account, "reader")

    # Failed grant must roll back and preserve the original owner authority.
    registry.require_account_access("alice", account, "manage")
    registry.grant_access("alice", "bob", account, "reader")
    registry.require_account_access("bob", account, "read")


def test_separate_registry_connections_serialize_concurrent_grant_changes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db_path = tmp_path / "concurrent-registry.db"
    first = AccountRegistry(db_path)
    second = AccountRegistry(db_path)
    try:
        first.create_principal("alice")
        first.create_principal("bob")
        account = first.create_account("alice", "concurrent-account", "active")

        def grant_many():
            for index in range(20):
                first.grant_access(
                    "alice", "bob", account, "reader" if index % 2 == 0 else "operator"
                )

        def revoke_many():
            for _ in range(20):
                second.revoke_access("alice", "bob", account)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(grant_many), pool.submit(revoke_many)]
            for future in futures:
                future.result(timeout=15)

        # Regardless of the final ordering of grant/revoke, the owner record
        # remains authoritative and no partial transaction damages the registry.
        first.require_account_access("alice", account, "manage")
        first.grant_access("alice", "bob", account, "reader")
        first.require_account_access("bob", account, "read")
        with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
            first.require_account_access("bob", account, "send")
    finally:
        first.close()
        second.close()


def test_failed_foreign_lifecycle_change_does_not_mutate_account(registry):
    account = pair(registry)
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.set_account_status("bob", account, "disconnected")

    # The denied attempt must leave the active account usable for its owner.
    registry.require_account_access("alice", account, "send")


def test_revoked_account_cannot_be_reactivated_through_owner_grant(registry):
    account = pair(registry)
    registry.set_account_status("alice", account, "revoked")

    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.set_account_status("alice", account, "active")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        registry.require_account_access("alice", account, "manage")


def test_registry_refuses_malformed_known_table_without_partial_ddl(tmp_path):
    import sqlite3

    db_path = tmp_path / "malformed-registry.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'fixture')")
        db.execute("CREATE TABLE whatsapp_accounts (account_id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="malformed table"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"schema_migrations", "whatsapp_accounts"}


def test_registry_refuses_known_columns_without_required_primary_key(tmp_path):
    import sqlite3

    db_path = tmp_path / "missing-key-registry.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'fixture')")
        db.execute(
            "CREATE TABLE whatsapp_accounts "
            "(account_id TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )

    with pytest.raises(RuntimeError, match="malformed table"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"schema_migrations", "whatsapp_accounts"}


def test_registry_refuses_known_table_without_lifecycle_check_constraints(tmp_path):
    import sqlite3

    db_path = tmp_path / "missing-check-registry.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'fixture')"
        )
        db.execute(
            "CREATE TABLE whatsapp_accounts ("
            "account_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    with pytest.raises(RuntimeError, match="malformed table"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"schema_migrations", "whatsapp_accounts"}


def test_registry_refuses_ownership_table_without_foreign_keys(tmp_path):
    import sqlite3

    db_path = tmp_path / "missing-foreign-keys-registry.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'fixture')"
        )
        db.execute(
            "CREATE TABLE platform_principals ("
            "principal_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL CHECK (status IN ('active', 'disabled')), "
            "created_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE whatsapp_accounts ("
            "account_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL CHECK (status IN "
            "('pending_pairing', 'active', 'disconnected', 'revoked', 'error')), "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE account_ownership ("
            "principal_id TEXT NOT NULL, account_id TEXT NOT NULL, "
            "role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'reader')), "
            "status TEXT NOT NULL CHECK (status IN ('active', 'revoked')), "
            "created_at TEXT NOT NULL, PRIMARY KEY (principal_id, account_id))"
        )

    with pytest.raises(RuntimeError, match="malformed table"):
        AccountRegistry(db_path)

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {
        "schema_migrations",
        "platform_principals",
        "whatsapp_accounts",
        "account_ownership",
    }


@pytest.mark.parametrize("invalid_account_id", ["", "   "])
def test_explicit_blank_account_id_is_rejected_without_creating_account(
    registry, invalid_account_id
):
    registry.create_principal("alice")

    with pytest.raises(ValueError, match="account_id must be a non-empty string"):
        registry.create_account("alice", invalid_account_id)

    # A rejected explicit identifier must not silently create an unrelated UUID.
    with registry._lock:
        rows = registry._db.execute("SELECT account_id FROM whatsapp_accounts").fetchall()
    assert rows == []
