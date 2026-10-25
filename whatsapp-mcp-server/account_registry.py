"""Isolated control-plane registry and deny-by-default account access policy.

This module is deliberately not wired into MCP tools or the WhatsApp bridge yet.
Construct it with an explicit database path in tests or a future service layer;
it never guesses or adopts the legacy WhatsApp database.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
PRINCIPAL_STATUSES: Final = frozenset({"active", "disabled"})
ACCOUNT_STATUSES: Final = frozenset(
    {"pending_pairing", "active", "disconnected", "revoked", "error"}
)
OWNERSHIP_ROLES: Final = frozenset({"owner", "operator", "reader"})
OWNERSHIP_STATUSES: Final = frozenset({"active", "revoked"})
ACTIONS: Final = frozenset({"read", "send", "manage"})

_ROLE_ACTIONS: Final = {
    "owner": ACTIONS,
    "operator": frozenset({"read", "send"}),
    "reader": frozenset({"read"}),
}
_ACCOUNT_ACTIONS: Final = {
    "pending_pairing": frozenset({"manage"}),
    "active": ACTIONS,
    "disconnected": frozenset({"read", "manage"}),
    "revoked": frozenset(),
    "error": frozenset({"manage"}),
}


class AuthorizationDenied(PermissionError):
    """Generic denial; deliberately does not reveal why an account was denied."""


class AccountRegistry:
    """SQLite registry for principals, accounts, and explicit ownership grants.

    The database path is mandatory. A single serialized connection keeps
    transactions predictable for this small initial control-plane store. Call
    close() when the owning service shuts down.
    """

    def __init__(self, db_path: str | Path):
        if not str(db_path).strip():
            raise ValueError("An explicit registry database path is required")
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.db_path, timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            registry_tables = {
                "schema_migrations",
                "platform_principals",
                "whatsapp_accounts",
                "account_ownership",
            }
            existing_tables = {
                row["name"]
                for row in self._db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            unrelated_tables = existing_tables - registry_tables
            if unrelated_tables:
                raise RuntimeError(
                    "Registry database contains unrelated tables; refusing to initialize"
                )
            if "schema_migrations" in existing_tables:
                versions = [
                    row["version"]
                    for row in self._db.execute("SELECT version FROM schema_migrations")
                ]
                if versions and max(versions) > SCHEMA_VERSION:
                    raise RuntimeError(
                        "Registry schema is newer than this application supports"
                    )
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS platform_principals (
                    principal_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS whatsapp_accounts (
                    account_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending_pairing', 'active', 'disconnected', 'revoked', 'error')
                    ),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS account_ownership (
                    principal_id TEXT NOT NULL REFERENCES platform_principals(principal_id),
                    account_id TEXT NOT NULL REFERENCES whatsapp_accounts(account_id),
                    role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'reader')),
                    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY (principal_id, account_id)
                );
                CREATE INDEX IF NOT EXISTS idx_account_ownership_account_status
                    ON account_ownership (account_id, status);
                """
            )
            versions = {
                row["version"]
                for row in self._db.execute("SELECT version FROM schema_migrations")
            }
            if versions and max(versions) > SCHEMA_VERSION:
                raise RuntimeError("Registry schema is newer than this application supports")
            self._db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def create_principal(self, principal_id: str, status: str = "active") -> None:
        principal_id = self._required_id(principal_id, "principal_id")
        if status not in PRINCIPAL_STATUSES:
            raise ValueError("Invalid principal status")
        with self._lock:
            self._db.execute(
                "INSERT INTO platform_principals(principal_id, status) VALUES (?, ?)",
                (principal_id, status),
            )

    def set_principal_status(self, principal_id: str, status: str) -> None:
        if status not in PRINCIPAL_STATUSES:
            raise ValueError("Invalid principal status")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE platform_principals SET status = ? WHERE principal_id = ?",
                (status, self._required_id(principal_id, "principal_id")),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown principal")

    def create_account(
        self, owner_principal_id: str, account_id: str | None = None,
        status: str = "pending_pairing",
    ) -> str:
        owner = self._required_id(owner_principal_id, "owner_principal_id")
        account = self._required_id(account_id, "account_id") if account_id else str(uuid.uuid4())
        if status not in ACCOUNT_STATUSES:
            raise ValueError("Invalid account status")
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                principal = self._db.execute(
                    "SELECT status FROM platform_principals WHERE principal_id = ?", (owner,)
                ).fetchone()
                if principal is None or principal["status"] != "active":
                    raise AuthorizationDenied("Account creation denied")
                self._db.execute(
                    "INSERT INTO whatsapp_accounts(account_id, status) VALUES (?, ?)",
                    (account, status),
                )
                self._db.execute(
                    """INSERT INTO account_ownership
                       (principal_id, account_id, role, status) VALUES (?, ?, 'owner', 'active')""",
                    (owner, account),
                )
                self._db.execute("COMMIT")
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
        return account

    def grant_access(
        self, actor_principal_id: str, principal_id: str, account_id: str,
        role: str = "reader",
    ) -> None:
        """Grant reader/operator access; only the active account owner may do so."""
        actor = self._required_id(actor_principal_id, "actor_principal_id")
        principal = self._required_id(principal_id, "principal_id")
        account = self._required_id(account_id, "account_id")
        if role not in {"operator", "reader"}:
            raise ValueError("Only operator or reader grants are supported")
        with self._lock:
            self.require_account_access(actor, account, "manage")
            target = self._db.execute(
                "SELECT status FROM platform_principals WHERE principal_id = ?",
                (principal,),
            ).fetchone()
            if target is None or target["status"] != "active":
                raise AuthorizationDenied("Account access denied")
            self._db.execute(
                """INSERT INTO account_ownership(principal_id, account_id, role, status)
                   VALUES (?, ?, ?, 'active')
                   ON CONFLICT(principal_id, account_id) DO UPDATE SET
                       role = excluded.role, status = 'active'""",
                (principal, account, role),
            )

    def revoke_access(
        self, actor_principal_id: str, principal_id: str, account_id: str
    ) -> None:
        """Revoke a grant only when the actor is authorized to manage the account."""
        actor = self._required_id(actor_principal_id, "actor_principal_id")
        principal = self._required_id(principal_id, "principal_id")
        account = self._required_id(account_id, "account_id")
        with self._lock:
            self.require_account_access(actor, account, "manage")
            cursor = self._db.execute(
                """UPDATE account_ownership SET status = 'revoked'
                   WHERE principal_id = ? AND account_id = ? AND role != 'owner'""",
                (principal, account),
            )
            if cursor.rowcount != 1:
                raise AuthorizationDenied("Account access denied")

    def set_account_status(self, account_id: str, status: str) -> None:
        if status not in ACCOUNT_STATUSES:
            raise ValueError("Invalid account status")
        with self._lock:
            cursor = self._db.execute(
                """UPDATE whatsapp_accounts
                   SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE account_id = ?""",
                (status, self._required_id(account_id, "account_id")),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown account")

    def require_account_access(self, principal_id: str, account_id: str, action: str) -> None:
        """Raise one generic denial unless identity, grant, role, and state all allow it."""
        if action not in ACTIONS:
            raise AuthorizationDenied("Account access denied")
        try:
            principal = self._required_id(principal_id, "principal_id")
            account = self._required_id(account_id, "account_id")
        except ValueError as exc:
            raise AuthorizationDenied("Account access denied") from exc
        with self._lock:
            row = self._db.execute(
                """
                SELECT p.status AS principal_status, a.status AS account_status,
                       o.role, o.status AS ownership_status
                FROM platform_principals AS p
                JOIN account_ownership AS o ON o.principal_id = p.principal_id
                JOIN whatsapp_accounts AS a ON a.account_id = o.account_id
                WHERE p.principal_id = ? AND o.account_id = ?
                """,
                (principal, account),
            ).fetchone()
        if (
            row is None
            or row["principal_status"] != "active"
            or row["ownership_status"] != "active"
            or action not in _ROLE_ACTIONS[row["role"]]
            or action not in _ACCOUNT_ACTIONS[row["account_status"]]
        ):
            raise AuthorizationDenied("Account access denied")

    @staticmethod
    def _required_id(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()
