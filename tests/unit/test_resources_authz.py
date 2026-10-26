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


class _ResourceRegistry:
    def __init__(self):
        self.handlers = {}

    def resource(self, uri, **kwargs):
        def decorate(function):
            self.handlers[function.__name__] = function
            return function
        return decorate


def _read_scoped_token(monkeypatch):
    from fastmcp.server import dependencies

    monkeypatch.setattr(
        dependencies,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["whatsapp:read"]),
    )


def test_message_resource_redacts_downstream_exception_text(
    monkeypatch, caplog
):
    import json
    import whatsapp

    _read_scoped_token(monkeypatch)

    def fail_message_context(*args, **kwargs):
        raise RuntimeError("token=secret-token private message body")

    monkeypatch.setattr(whatsapp, "get_message_context", fail_message_context)
    registry = _ResourceRegistry()
    resources.register(registry)

    result = json.loads(registry.handlers["message_resource"]("message-id", "chat-jid"))
    assert result == {"error": "Unable to read message resource"}
    assert "secret-token" not in caplog.text
    assert "private message body" not in caplog.text
    assert "secret-token" not in str(result)


def test_media_resource_redacts_media_error_text(monkeypatch, caplog):
    import json
    import media

    _read_scoped_token(monkeypatch)

    def fail_fetch_media(*args, **kwargs):
        raise media.MediaError("https://internal.example/token=secret-token")

    monkeypatch.setattr(media, "fetch_media", fail_fetch_media)
    registry = _ResourceRegistry()
    resources.register(registry)

    result = registry.handlers["media_resource"]("message-id", "chat-jid")
    body = json.loads(result["blob"].decode())
    assert body == {"error": "Unable to read media resource"}
    assert "secret-token" not in caplog.text
    assert "internal.example" not in caplog.text
    assert "secret-token" not in str(body)


def test_chat_resource_redacts_downstream_exception_text(monkeypatch, caplog):
    import whatsapp

    from fastmcp.exceptions import ToolError

    _read_scoped_token(monkeypatch)

    def fail_get_chat(*args, **kwargs):
        raise RuntimeError("https://internal.example/token=secret-token")

    monkeypatch.setattr(whatsapp, "get_chat", fail_get_chat)
    registry = _ResourceRegistry()
    resources.register(registry)

    with pytest.raises(ToolError, match="Unable to read chat resource"):
        registry.handlers["chat_resource"]("chat-jid")
    assert "secret-token" not in caplog.text
    assert "internal.example" not in caplog.text


def test_media_resource_redacts_local_file_read_exception(monkeypatch, caplog):
    import json
    import media

    _read_scoped_token(monkeypatch)

    class FailingPath:
        def read_bytes(self):
            raise OSError("token=secret-token local=/private/messages")

    monkeypatch.setattr(
        media,
        "fetch_media",
        lambda *args, **kwargs: {"path": FailingPath(), "mime": "image/png"},
    )
    registry = _ResourceRegistry()
    resources.register(registry)

    result = registry.handlers["media_resource"]("message-id", "chat-jid")
    body = json.loads(result["blob"].decode())
    assert body == {"error": "Unable to read media resource"}
    assert "secret-token" not in caplog.text
    assert "/private/messages" not in caplog.text
