"""Reject ambiguous or unsafe multi-token configuration during server startup."""

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


@pytest.mark.parametrize(
    ("entries", "expected_error"),
    [
        (
            [
                {"token": "same-secret", "client_id": "client-a", "scopes": ["whatsapp:read"]},
                {"token": "same-secret", "client_id": "client-b", "scopes": ["whatsapp:read"]},
            ],
            "duplicate token",
        ),
        (
            [
                {"token": "secret-a", "client_id": "same-client", "scopes": ["whatsapp:read"]},
                {"token": "secret-b", "client_id": "same-client", "scopes": ["whatsapp:send"]},
            ],
            "duplicate client_id",
        ),
        (
            [{"token": "secret-a", "client_id": "client-a", "scopes": ["whatsapp:root"]}],
            "unsupported scopes",
        ),
        (
            [{"token": "secret-a", "client_id": "client-a", "scopes": []}],
            "non-empty list of scopes",
        ),
        (
            [{"token": "secret-a", "client_id": "client-a", "scopes": ["whatsapp:read", "whatsapp:read"]}],
            "duplicate scopes",
        ),
        (
            ["not-an-object"],
            "must be a JSON object",
        ),
    ],
)
def test_invalid_multi_token_configuration_fails_closed(tmp_path, entries, expected_error):
    for name in ("messages.db", "whatsapp.db"):
        sqlite3.connect(tmp_path / name).close()
    env = {
        **os.environ,
        "WHATSAPP_MCP_TOKEN": "",
        "WHATSAPP_MCP_TOKENS_JSON": json.dumps(entries),
        "WHATSAPP_MCP_TOOLSET": "minimal",
        "MESSAGES_DB_PATH": str(tmp_path / "messages.db"),
        "WHATSMEOW_DB_PATH": str(tmp_path / "whatsapp.db"),
        "WHATSAPP_MCP_SHORT_DB": str(tmp_path / "short.db"),
        "WHATSAPP_MCP_SCHEDULE_DB": str(tmp_path / "schedule.db"),
        "WHATSAPP_API_BASE_URL": "http://127.0.0.1:9/api",
        "WHATSAPP_MCP_AUDIT_LOG": str(tmp_path / "audit.jsonl"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import server"],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode != 0, "invalid token configuration unexpectedly started"
    assert expected_error in proc.stderr, proc.stderr[-3000:]
