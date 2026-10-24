from __future__ import annotations

from types import SimpleNamespace

import pytest

import auth_identity
from account_authorization import require_current_request_account_access
from account_registry import AccountRegistry, AuthorizationDenied
from auth_identity import AuthenticatedIdentityError
from principal_mapping import PrincipalMappingError


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


def test_current_request_maps_verified_client_before_account_authorization(registry, monkeypatch):
    set_verified_client(monkeypatch, "alice-app")
    mapping = {"alice-app": "alice", "bob-app": "bob"}
    assert require_current_request_account_access(
        registry, "alice-account", "read", mapping
    ) == "alice"


def test_foreign_account_is_denied_even_with_valid_authenticated_client(registry, monkeypatch):
    set_verified_client(monkeypatch, "bob-app")
    mapping = {"alice-app": "alice", "bob-app": "bob"}
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        require_current_request_account_access(
            registry, "alice-account", "read", mapping
        )


def test_missing_identity_denies_before_registry_access(registry, monkeypatch):
    monkeypatch.setattr(auth_identity, "get_access_token", lambda: None)
    with pytest.raises(AuthenticatedIdentityError):
        require_current_request_account_access(
            registry, "alice-account", "read", {"alice-app": "alice"}
        )


def test_unmapped_client_is_denied_without_client_id_fallback(registry, monkeypatch):
    set_verified_client(monkeypatch, "alice")
    with pytest.raises(PrincipalMappingError, match="Authenticated principal unavailable"):
        require_current_request_account_access(
            registry, "alice-account", "read", {"alice-app": "alice"}
        )


def test_mapping_to_unprovisioned_principal_is_denied_by_registry(registry, monkeypatch):
    set_verified_client(monkeypatch, "new-app")
    with pytest.raises(AuthorizationDenied, match="^Account access denied$"):
        require_current_request_account_access(
            registry, "alice-account", "read", {"new-app": "not-provisioned"}
        )
