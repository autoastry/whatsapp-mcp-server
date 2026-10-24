from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import observability


def _context(tool_name: str):
    return SimpleNamespace(message=SimpleNamespace(name=tool_name, arguments={}))


def test_authz_middleware_allows_tool_when_required_scope_is_present(monkeypatch):
    monkeypatch.setattr(
        observability,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["whatsapp:authenticated", "whatsapp:read"]),
    )
    called = []

    async def call_next(context):
        called.append(context.message.name)
        return "allowed"

    result = asyncio.run(
        observability.AuthzMiddleware().on_call_tool(_context("list_chats"), call_next)
    )

    assert result == "allowed"
    assert called == ["list_chats"]


def test_authz_middleware_denies_read_only_token_for_send_tool(monkeypatch):
    monkeypatch.setattr(
        observability,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["whatsapp:authenticated", "whatsapp:read"]),
    )
    called = []

    async def call_next(_context):
        called.append(True)
        return "should not run"

    with pytest.raises(ToolError, match="Insufficient scope"):
        asyncio.run(
            observability.AuthzMiddleware().on_call_tool(
                _context("send_message"), call_next
            )
        )

    assert called == []


def test_authz_middleware_fails_closed_for_unmapped_tool_even_for_full_scope(monkeypatch):
    monkeypatch.setattr(
        observability,
        "get_access_token",
        lambda: SimpleNamespace(scopes=["whatsapp:full"]),
    )

    async def call_next(_context):
        raise AssertionError("unmapped tool must not execute")

    with pytest.raises(ToolError, match="Authorization policy missing"):
        asyncio.run(
            observability.AuthzMiddleware().on_call_tool(
                _context("new_tool_without_policy"), call_next
            )
        )


def test_authz_middleware_fails_closed_when_request_token_context_is_missing(monkeypatch):
    def no_context():
        raise RuntimeError("no request context")

    monkeypatch.setattr(observability, "get_access_token", no_context)

    async def call_next(_context):
        raise AssertionError("tool must not execute without token context")

    with pytest.raises(ToolError, match="Unable to resolve authenticated caller"):
        asyncio.run(
            observability.AuthzMiddleware().on_call_tool(
                _context("list_chats"), call_next
            )
        )
