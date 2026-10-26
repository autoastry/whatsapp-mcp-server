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

def test_every_registered_mcp_tool_has_an_explicit_scope_policy():
    """New tools must not silently ship without an authorization policy."""
    import ast
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2] / "whatsapp-mcp-server"
    registered = set()
    for source_path in project_root.rglob("*.py"):
        # uv creates the dependency environment inside the project directory in
        # CI. Scan first-party source only; dependency tools are not our registry.
        relative_parts = source_path.relative_to(project_root).parts
        if any(part in {".venv", "site-packages", "__pycache__"} for part in relative_parts):
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                # Support both @mcp.tool and @mcp.tool(...). Otherwise a new
                # bare decorator could bypass this source-level policy audit.
                func = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "tool"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "mcp"
                ):
                    continue
                tool_name = node.name
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                            if isinstance(keyword.value.value, str):
                                tool_name = keyword.value.value
                registered.add(tool_name)

    assert registered, "test must discover registered MCP tools"
    assert registered <= set(observability.TOOL_SCOPES), (
        f"Registered tools missing scope policies: "
        f"{sorted(registered - set(observability.TOOL_SCOPES))}"
    )



def test_every_send_or_admin_scoped_tool_is_audited():
    """Mutating operations must not bypass audit on direct or call_tool dispatch."""
    mutating = {
        name
        for name, scope in observability.TOOL_SCOPES.items()
        if scope in {"whatsapp:send", "whatsapp:admin"}
    }
    assert mutating <= observability.AUDIT_MUTATING_TOOLS, (
        "Mutating tools missing audit policy: "
        f"{sorted(mutating - observability.AUDIT_MUTATING_TOOLS)}"
    )
