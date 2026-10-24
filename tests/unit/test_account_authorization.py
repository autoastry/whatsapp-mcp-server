from __future__ import annotations

from types import SimpleNamespace

import pytest

import auth_identity
from account_authorization import require_current_request_account_access
from account_registry import AccountRegistry, AuthorizationDenied
from auth_identity import AuthenticatedIdentityError


@pytest.fixture()
def registry(tmp_path):
    value = AccountRegistry(tmp_path / "platform.db")
    value.create_principal("alice")
    value.create_principal("bob")
    value.create_account("alice", "alice-account", "active")
    yield value
    value.close()


def set_verified_client(monkeypatch, client_id):
    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(client_id=client_id),
    )


def test_current_request_identity_is_used_for_account_authorization(registry, monkeypatch):
    set_verified_client(monkeypatch, "alice")
    assert require_current_request_account_access(registry, "alice-account", "read") == "alice"


def test_foreign_account_is_denied_even_with_valid_authenticated_client(registry, monkeypatch):
    set_verified_client(monkeypatch, "bob")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        require_current_request_account_access(registry, "alice-account", "read")


def test_missing_identity_denies_before_registry_access(registry, monkeypatch):
    monkeypatch.setattr(auth_identity, "get_access_token", lambda: None)
    with pytest.raises(AuthenticatedIdentityError):
        require_current_request_account_access(registry, "alice-account", "read")
