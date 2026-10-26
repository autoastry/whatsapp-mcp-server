from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_passes_multi_token_config_without_requiring_legacy_token():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "WHATSAPP_MCP_TOKEN: ${WHATSAPP_MCP_TOKEN:-}" in compose
    assert "WHATSAPP_MCP_TOKENS_JSON: ${WHATSAPP_MCP_TOKENS_JSON:-}" in compose
    assert "${WHATSAPP_MCP_TOKEN:?" not in compose


def test_env_example_multi_token_value_is_a_valid_parser_compatible_array():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    line = next(
        line
        for line in env_example.splitlines()
        if line.startswith("# WHATSAPP_MCP_TOKENS_JSON=")
    )
    raw_value = line.split("=", 1)[1].strip()
    assert raw_value.startswith("'") and raw_value.endswith("'")
    entries = json.loads(raw_value[1:-1])

    assert isinstance(entries, list)
    assert entries
    for entry in entries:
        assert isinstance(entry.get("token"), str) and entry["token"]
        assert isinstance(entry.get("client_id"), str) and entry["client_id"]
        assert isinstance(entry.get("scopes"), list) and entry["scopes"]
