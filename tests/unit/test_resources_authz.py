from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import resources


@pytest.mark.parametrize(
    "scopes",
    [
        ["whatsapp:read"],
        ["whatsapp:authenticated", "whatsapp:read"],
        ["whatsapp:full"],
    ],
)
def test_resource_read_scope_allows_read_or_explicit_full_scope(monkeypatch, scopes):
    from fastmcp.server import dependencies

    monkeypatch.setattr(
        dependencies, "get_access_token", lambda: SimpleNamespace(scopes=scopes)
    )
    assert resources._require_resource_read_scope() is None


@pytest.mark.parametrize(
    "scopes",
    [
        ["whatsapp:authenticated"],
        ["whatsapp:authenticated", "whatsapp:send"],
        ["whatsapp:admin"],
        [],
    ],
)
def test_resource_read_scope_denies_tokens_without_read_permission(monkeypatch, scopes):
    from fastmcp.server import dependencies

    monkeypatch.setattr(
        dependencies, "get_access_token", lambda: SimpleNamespace(scopes=scopes)
    )
    with pytest.raises(ToolError, match="requires 'whatsapp:read'"):
        resources._require_resource_read_scope()


def test_resource_read_scope_fails_closed_without_authenticated_context(monkeypatch):
    from fastmcp.server import dependencies

    monkeypatch.setattr(dependencies, "get_access_token", lambda: None)
    with pytest.raises(ToolError, match="context is missing"):
        resources._require_resource_read_scope()


def test_resource_read_scope_fails_closed_when_context_lookup_raises(monkeypatch):
    from fastmcp.server import dependencies

    def no_context():
        raise RuntimeError("no request context")

    monkeypatch.setattr(dependencies, "get_access_token", no_context)
    with pytest.raises(ToolError, match="Unable to resolve authenticated caller"):
        resources._require_resource_read_scope()


def test_every_data_resource_checks_scope_before_reading_data():
    source_path = Path(__file__).resolve().parents[2] / "whatsapp-mcp-server" / "resources.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    for name in ("chat_resource", "message_resource", "media_resource"):
        node = functions[name]
        statements = node.body
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]
        first = statements[0]
        assert isinstance(first, ast.Expr)
        assert isinstance(first.value, ast.Call)
        assert isinstance(first.value.func, ast.Name)
        assert first.value.func.id == "_require_resource_read_scope"
