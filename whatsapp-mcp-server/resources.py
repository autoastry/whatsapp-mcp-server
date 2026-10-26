"""N4: MCP resources for chat, message, and media.

FastMCP treats URIs with `{param}` segments as templated resources. Clients
call resources/list to discover templates and resources/read with a
concrete URI. Exposing our data via resources lets Claude Desktop's attach
UI use them directly instead of having to synthesize tool call chains.
"""
from __future__ import annotations

import json
import logging
from typing import Any


log = logging.getLogger("whatsapp_mcp.res")


def _require_resource_read_scope() -> None:
    """Fail closed: resource reads need read scope, not only transport auth."""
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception as exc:
        raise ToolError("Unable to resolve authenticated caller") from exc
    if token is None:
        raise ToolError("Authenticated caller context is missing")
    scopes = list(getattr(token, "scopes", []) or [])
    if "whatsapp:full" not in scopes and "whatsapp:read" not in scopes:
        raise ToolError("Insufficient scope: resource requires 'whatsapp:read'")


def register(mcp) -> None:
    from whatsapp import (
        get_chat as _get_chat,
        list_messages as _list_messages,
    )
    # media.fetch_media, not whatsapp.download_media: the bridge cannot serve
    # revoked media and its media-retry fallback needs the sender's phone
    # online, so resources hit the same failures the tools used to.
    from media import MediaError, fetch_media as _fetch_media

    @mcp.resource(
        "chat://{chat_jid}",
        name="WhatsApp chat",
        description="Chat metadata plus the last 20 messages.",
        mime_type="application/json",
    )
    def chat_resource(chat_jid: str) -> str:
        _require_resource_read_scope()
        try:
            chat = _get_chat(chat_jid, include_last_message=True)
            msgs = _list_messages(chat_jid=chat_jid, limit=20, include_context=False)
            # dataclasses -> dict
            chat_d = chat.__dict__ if chat else None
            if chat_d and chat_d.get("last_message_time"):
                chat_d["last_message_time"] = chat_d["last_message_time"].isoformat()
            msg_ds = []
            for m in (msgs or []):
                d = m.__dict__.copy()
                if d.get("timestamp"):
                    d["timestamp"] = d["timestamp"].isoformat()
                msg_ds.append(d)
            return json.dumps({"chat": chat_d, "messages": msg_ds}, ensure_ascii=False)
        except Exception as exc:
            log.warning("chat resource read failed (%s)", type(exc).__name__)
            from fastmcp.exceptions import ToolError

            raise ToolError("Unable to read chat resource") from exc

    @mcp.resource(
        "message://{message_id}/{chat_jid}",
        name="WhatsApp message",
        description="Single message by id + chat_jid.",
        mime_type="application/json",
    )
    def message_resource(message_id: str, chat_jid: str) -> str:
        _require_resource_read_scope()
        from whatsapp import get_message_context as _get_ctx
        try:
            ctx = _get_ctx(message_id, before=0, after=0)
            m = ctx.message
            d = m.__dict__.copy()
            if d.get("timestamp"):
                d["timestamp"] = d["timestamp"].isoformat()
            return json.dumps(d, ensure_ascii=False)
        except Exception as exc:
            log.warning("message resource read failed (%s)", type(exc).__name__)
            return json.dumps({"error": "Unable to read message resource"})

    @mcp.resource(
        "media://{message_id}/{chat_jid}",
        name="WhatsApp media",
        description="Media bytes attached to a message (image / audio / video / doc).",
        mime_type="application/octet-stream",
    )
    def media_resource(message_id: str, chat_jid: str) -> dict[str, Any]:
        """Returns a BlobResourceContents payload."""
        _require_resource_read_scope()
        try:
            info = _fetch_media(message_id, chat_jid)
            import base64

            return {
                "blob": base64.b64encode(info["path"].read_bytes()).decode("ascii"),
                "mimeType": info.get("mime", "application/octet-stream"),
            }
        except Exception as exc:
            # Keep bridge paths, URLs, and downstream exception text out of the
            # resource body, including local file-read errors after fetching.
            log.warning("media resource read failed (%s)", type(exc).__name__)
            return {
                "blob": json.dumps({"error": "Unable to read media resource"}).encode(),
                "mimeType": "application/json",
            }
