"""Exercise verified token context through the actual Streamable HTTP ASGI transport.

This boots the app in a subprocess with temporary database paths and no WhatsApp
bridge. A probe tool reads FastMCP's request-scoped access token; the assertion
therefore covers transport authentication and request-context propagation rather
than only mocking get_access_token().
"""
from __future__ import annotations

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
import server
from fastmcp import Client
from fastmcp.server.dependencies import get_access_token
from observability import TOOL_SCOPES

# A test-only tool makes the request's verified auth context observable. It
# returns no WhatsApp data and does not access the bridge or shared databases.
TOOL_SCOPES["auth_context_probe"] = "whatsapp:read"

@server.mcp.tool(name="auth_context_probe")
def auth_context_probe() -> str:
    token = get_access_token()
    return token.client_id + "|" + ",".join(token.scopes)

async def main():
    async with Client(server.asgi_app, auth="test-token") as client:
        result = await client.call_tool("auth_context_probe", {})
        print(result.content[0].text)

asyncio.run(main())
"""


def test_http_transport_propagates_verified_token_context(tmp_path):
    for name in ("messages.db", "whatsapp.db"):
        sqlite3.connect(tmp_path / name).close()

    env = {
        **os.environ,
        "WHATSAPP_MCP_TOKEN": "test-token",
        "WHATSAPP_MCP_TOOLSET": "minimal",
        "MESSAGES_DB_PATH": str(tmp_path / "messages.db"),
        "WHATSMEOW_DB_PATH": str(tmp_path / "whatsapp.db"),
        "WHATSAPP_MCP_SHORT_DB": str(tmp_path / "short.db"),
        "WHATSAPP_MCP_SCHEDULE_DB": str(tmp_path / "schedule.db"),
        "WHATSAPP_API_BASE_URL": "http://127.0.0.1:9/api",
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
    output = proc.stdout.strip().splitlines()[-1]
    assert output.startswith("owner|"), output
    scopes = output.partition("|")[2].split(",")
    assert "whatsapp:authenticated" in scopes, output
    assert "whatsapp:full" in scopes, output
