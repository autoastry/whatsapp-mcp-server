"""Exercise verified token context and scope enforcement over Streamable HTTP.

The app runs in a subprocess with temporary database paths and no WhatsApp
bridge. Test-only tools expose the verified request token and verify that a
read-only credential cannot call a send-scoped tool.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

SERVER_DIR = Path(__file__).resolve().parents[2] / "whatsapp-mcp-server"

PROBE = r"""
import asyncio
import socket

import server
import uvicorn
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from observability import TOOL_SCOPES

# These tools are test-only and never access WhatsApp data or the bridge.
TOOL_SCOPES["auth_context_probe"] = "whatsapp:read"
TOOL_SCOPES["authz_scope_probe"] = "whatsapp:send"

@server.mcp.tool(name="auth_context_probe")
def auth_context_probe() -> str:
    token = get_access_token()
    return token.client_id + "|" + ",".join(token.scopes)

@server.mcp.tool(name="authz_scope_probe")
def authz_scope_probe() -> str:
    return "SHOULD_NOT_EXECUTE"

async def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = uvicorn.Config(
        server.asgi_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="on",
    )
    http_server = uvicorn.Server(config)
    server_task = asyncio.create_task(http_server.serve())
    try:
        for _ in range(500):
            if http_server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.02)
        assert http_server.started, "Uvicorn did not start"

        async with Client(f"http://127.0.0.1:{port}/mcp", auth="read-only-secret") as client:
            context_result = await client.call_tool("auth_context_probe", {})
            print("CONTEXT=" + context_result.content[0].text)
            try:
                await client.call_tool("authz_scope_probe", {})
            except ToolError as exc:
                print("DENIED=True|" + str(exc))
            else:
                print("DENIED=False|scope-restricted tool unexpectedly executed")
    finally:
        http_server.should_exit = True
        await server_task

asyncio.run(main())
"""


def test_http_transport_propagates_verified_token_and_enforces_scopes(tmp_path):
    for name in ("messages.db", "whatsapp.db"):
        sqlite3.connect(tmp_path / name).close()

    env = {
        **os.environ,
        "WHATSAPP_MCP_TOKENS_JSON": json.dumps(
            [
                {
                    "token": "read-only-secret",
                    "client_id": "reader-app",
                    "scopes": ["whatsapp:read"],
                }
            ]
        ),
        "WHATSAPP_MCP_TOKEN": "",
        "WHATSAPP_MCP_TOOLSET": "minimal",
        "MESSAGES_DB_PATH": str(tmp_path / "messages.db"),
        "WHATSMEOW_DB_PATH": str(tmp_path / "whatsapp.db"),
        "WHATSAPP_MCP_SHORT_DB": str(tmp_path / "short.db"),
        "WHATSAPP_MCP_SCHEDULE_DB": str(tmp_path / "schedule.db"),
        "WHATSAPP_API_BASE_URL": "http://127.0.0.1:9/api",
        "WHATSAPP_MCP_AUDIT_LOG": str(tmp_path / "audit.jsonl"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr[-4000:]
    lines = proc.stdout.strip().splitlines()
    context = next(line.partition("CONTEXT=")[2] for line in lines if "CONTEXT=" in line)
    assert context.startswith("reader-app|"), context
    scopes = context.partition("|")[2].split(",")
    assert "whatsapp:authenticated" in scopes, context
    assert "whatsapp:read" in scopes, context
    assert "whatsapp:send" not in scopes, context

    denied = next(line.partition("DENIED=")[2] for line in lines if "DENIED=" in line)
    assert denied.startswith("True|"), denied
    assert "Insufficient scope" in denied, denied
    assert "SHOULD_NOT_EXECUTE" not in denied, denied
