from __future__ import annotations

import pytest

from principal_mapping import (
    PrincipalMappingError,
    parse_principal_mapping,
    resolve_principal_id,
)


def test_explicit_mapping_resolves_verified_client_to_distinct_principal():
    mapping = parse_principal_mapping(
        '{"mobile-app":"owner-123","reporting-app":"reader-456"}'
    )
    assert resolve_principal_id("mobile-app", mapping) == "owner-123"
    assert resolve_principal_id("reporting-app", mapping) == "reader-456"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-json",
        "[]",
        "{}",
        '{"client-a": ""}',
        '{" ": "owner-a"}',
    ],
)
def test_invalid_or_ambiguous_mapping_is_rejected(raw):
    with pytest.raises(PrincipalMappingError):
        parse_principal_mapping(raw)


@pytest.mark.parametrize("client_id", [None, "", " ", "unknown-client"])
def test_unknown_or_missing_client_never_falls_back_to_a_principal(client_id):
    mapping = {"known-client": "owner-a"}
    with pytest.raises(PrincipalMappingError, match="Authenticated principal unavailable"):
        resolve_principal_id(client_id, mapping)


def test_multiple_clients_may_map_to_one_stable_principal():
    mapping = parse_principal_mapping(
        '{"mobile-app":"owner-a","desktop-app":"owner-a"}'
    )
    assert resolve_principal_id("mobile-app", mapping) == "owner-a"
    assert resolve_principal_id("desktop-app", mapping) == "owner-a"


def test_duplicate_json_client_keys_are_rejected():
    with pytest.raises(PrincipalMappingError, match="Duplicate client ID"):
        parse_principal_mapping('{"client-a":"owner-a","client-a":"owner-b"}')


def test_mapping_does_not_treat_client_id_as_principal_by_default():
    mapping = {"known-client": "stable-principal"}
    assert resolve_principal_id("known-client", mapping) == "stable-principal"
    with pytest.raises(PrincipalMappingError):
        resolve_principal_id("stable-principal", mapping)
