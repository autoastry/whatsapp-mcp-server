from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import auth_identity
import observability
import toolsearch


def test_observability_middleware_audit_uses_verified_client_id(monkeypatch, tmp_path):
    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(client_id="client-blue", scopes=["whatsapp:send"]),
    )
    path = tmp_path / "middleware-audit.jsonl"
    monkeypatch.setattr(observability, "AUDIT_LOG_PATH", path)
    context = SimpleNamespace(
        message=SimpleNamespace(name="send_message", arguments={"text": "private"}),
    )

    async def call_next(_context):
        return "ok"

    result = asyncio.run(observability.ObservabilityMiddleware().on_call_tool(context, call_next))

    assert result == "ok"
    payload = path.read_text(encoding="utf-8")
    row = json.loads(payload.splitlines()[0])
    assert row["client_id"] == "client-blue"
    assert row["tool"] == "send_message"
    assert "private" not in payload


def test_call_tool_audit_uses_verified_client_id(monkeypatch, tmp_path):
    monkeypatch.setattr(
        auth_identity, "get_access_token",
        lambda: SimpleNamespace(client_id="client-green", scopes=["whatsapp:send"]),
    )
    path = tmp_path / "dispatch-audit.jsonl"
    monkeypatch.setattr(observability, "AUDIT_LOG_PATH", path)

    toolsearch._audit("send_message", {"text": "private"}, "ok", 12.5)

    payload = path.read_text(encoding="utf-8")
    row = json.loads(payload.splitlines()[0])
    assert row["client_id"] == "client-green"
    assert row["via"] == "call_tool"
    assert row["tool"] == "send_message"
    assert "private" not in payload
