from __future__ import annotations

from types import SimpleNamespace

import pytest

import auth_identity
from auth_identity import AuthenticatedIdentityError, require_authenticated_principal_id


def test_resolves_principal_from_verified_token_client_id(monkeypatch):
    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(client_id=" account-owner ", token="secret"),
    )
    assert require_authenticated_principal_id() == "account-owner"


@pytest.mark.parametrize("token", [None, SimpleNamespace(client_id=None),
                                   SimpleNamespace(client_id=""),
                                   SimpleNamespace(client_id="   "),
                                   SimpleNamespace(claims={"sub": "claim-user"})])
def test_missing_or_unusable_client_id_fails_closed(monkeypatch, token):
    monkeypatch.setattr(auth_identity, "get_access_token", lambda: token)
    with pytest.raises(AuthenticatedIdentityError, match="Authenticated principal unavailable"):
        require_authenticated_principal_id()


def test_access_token_context_failure_fails_closed(monkeypatch):
    def fail():
        raise RuntimeError("no request context")

    monkeypatch.setattr(auth_identity, "get_access_token", fail)
    with pytest.raises(AuthenticatedIdentityError, match="Authenticated principal unavailable"):
        require_authenticated_principal_id()


def test_audit_client_id_uses_verified_client_id_or_unknown(monkeypatch):
    from auth_identity import audit_client_id_or_unknown

    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(client_id="client-b"),
    )
    assert audit_client_id_or_unknown() == "client-b"

    monkeypatch.setattr(auth_identity, "get_access_token", lambda: None)
    assert audit_client_id_or_unknown() == "unknown"


def test_audit_client_id_does_not_invent_owner(monkeypatch):
    from auth_identity import audit_client_id_or_unknown

    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(scopes=["whatsapp:full"]),
    )
    assert audit_client_id_or_unknown() == "unknown"
