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



def _compose_service_block(compose: str, service_name: str, next_service: str) -> str:
    start = compose.index(f"  {service_name}:")
    end = compose.index(f"  {next_service}:", start + 1)
    return compose[start:end]


def test_bridge_rest_api_is_not_published_to_host_and_mcp_is_loopback_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    bridge = _compose_service_block(compose, "bridge", "mcp")
    mcp = _compose_service_block(compose, "mcp", "autoheal")

    bridge_keys = {
        line.strip()
        for line in bridge.splitlines()
        if line.startswith("    ") and not line.startswith("      ")
    }
    assert "expose:" in bridge_keys
    assert "ports:" not in bridge_keys

    mcp_port_lines = [
        line.strip()
        for line in mcp.splitlines()
        if line.strip().startswith("- ")
        and ("9000" in line or "9100" in line)
    ]
    assert any('127.0.0.1:9100:9000' in line for line in mcp_port_lines)
    assert not any('"9100:9000"' in line for line in mcp_port_lines)
