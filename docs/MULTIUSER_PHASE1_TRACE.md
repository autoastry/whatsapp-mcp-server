# Phase 1 Source Trace — Multi-user WhatsApp MCP

Date: 2026-10-23
Branch: `feature/multi-user-platform-audit`
Status: source inspection addendum; no application code changed

## Purpose and evidence boundary

This addendum records concrete source-level observations found while continuing Phase 1. It is not a runtime test report. Files were read through GitHub's file API; no local checkout, shell, container, WhatsApp account, or CI runner was available here. Do not treat any unexecuted test as passing.

## Confirmed findings

### 1. Authentication has multiple credentials, not multiple principals

In `whatsapp-mcp-server/server.py`, `WHATSAPP_MCP_TOKENS_JSON` entries are stored in `_TOKENS` with `client_id` and `scopes`; there is no first-class `principal_id`/account ownership mapping in the parsed metadata. The legacy `WHATSAPP_MCP_TOKEN` maps to `client_id="owner"`. This is credential separation only; all valid tokens still reach the same process-level WhatsApp resources.

Specific hardening items:
- Reject duplicate token entries and malformed entry shapes rather than silently overwriting/skipping entries.
- Validate scopes against an allowlist and reject blank/duplicate client/principal IDs.
- Add a trusted, stable principal identity to token metadata. Never infer it from tool arguments.
- Add revocation/disable semantics and tests.

Source: `whatsapp-mcp-server/server.py`.

### 2. Scope enforcement has fail-open paths that need closure before tenancy

`observability.py` defines `TOOL_SCOPES` and the direct-call `AuthzMiddleware`. The middleware checks a scope only when the tool name exists in that map. In `toolsearch.py`, `_enforce_scope` catches any exception importing `TOOL_SCOPES` and returns, and also returns when no scope mapping exists. That is a fail-open fallback for the meta-tool dispatcher. The current map must be mechanically compared against the complete registered tool library, not assumed complete.

Specific hardening items:
- Make missing scope metadata a deny-by-default error for every callable non-meta tool.
- Remove the catch-and-allow import fallback; startup should fail if authorization configuration cannot load.
- Keep direct tool calls and `call_tool` dispatch on one shared authorization function.
- Add a test that enumerates all registered tools and proves each has a policy; test unknown and intentionally unmapped tool names are denied.

Sources: `whatsapp-mcp-server/observability.py`, `whatsapp-mcp-server/toolsearch.py`.

### 3. Audit identity is hard-coded

Both the observability audit row and the internal `call_tool` audit row use `client_id: "owner"`. With multiple credentials, this loses caller attribution. The args hash avoids writing raw arguments, but a hash is not a substitute for a trusted principal/account identity.

Specific hardening items:
- Read the verified principal/client identity from the authenticated request context.
- Record opaque principal/account IDs, tool, outcome, duration, and a redacted/allowlisted metadata set.
- Keep message bodies, tokens, QR payloads, media keys, and signed URLs out of audit rows.
- Test that separate principals create distinguishable audit records without leaking secrets.

Sources: `whatsapp-mcp-server/observability.py`, `whatsapp-mcp-server/toolsearch.py`.

### 4. WhatsApp message storage is one shared database

`whatsapp-bridge/main.go`'s `NewMessageStore` opens `store/messages.db` and creates the chats/messages tables plus FTS, reactions, poll, group participant, profile picture, and other derived tables in that database. Rows are keyed around chat JIDs/message IDs, not an authenticated platform principal. `whatsapp-mcp-server/whatsapp.py` has one module-level `MESSAGES_DB_PATH` and a shared HTTP session for the bridge. The Compose deployment mounts the same `wa-store` volume into the bridge read/write and MCP container read-only.

This is a strong confirmed single-account assumption. A shared database with caller-supplied JIDs is not account isolation. Per-account store selection must be established before any read or side effect; do not rely on adding only an account ID to a request header.

Sources: `whatsapp-bridge/main.go`, `whatsapp-mcp-server/whatsapp.py`, `docker-compose.yml`.

### 5. Additional state is outside the message database

- `whatsapp-mcp-server/scheduling.py` uses one configured/fallback SQLite database for scheduled sends and drafts; scheduled records do not currently carry a platform account/principal identity in the shown schema/API.
- `whatsapp-mcp-server/identity.py` loads a process-global LID/phone/name mapping from one `WHATSMEOW_DB_PATH` and caches it in module-level dictionaries. This mapping is derived from one WhatsApp store.
- `whatsapp-mcp-server/media.py` defaults media to one `/data/media` root. Signed URL claims need explicit account binding and account-aware authorization before reading cached/decrypted media.
- `docker-compose.yml` mounts shared `wa-store`, `wa-audit`, and `wa-media` volumes. Network privacy protects the bridge port from direct host publication but does not enforce account ownership between authenticated MCP callers.

These paths must be part of the tenant inventory, including transcription results, short URLs, caches, temp files, and worker queues, before any storage migration.

Sources: `whatsapp-mcp-server/scheduling.py`, `whatsapp-mcp-server/identity.py`, `whatsapp-mcp-server/media.py`, `docker-compose.yml`.

### 6. Response caching needs a tenant-safety proof

`server.py` enables `ResponseCachingMiddleware` for read tools with a 30-second TTL. The configuration shown is an allowlist of tool names and does not explicitly specify principal/account partitioning. Before enabling multi-user requests, inspect the exact FastMCP cache-key behavior for the locked dependency version and add a cross-principal cache-isolation test; if that cannot be proved, disable response caching for account-dependent tools until the key includes trusted principal and account identity.

Source: `whatsapp-mcp-server/server.py`.

### 7. Existing deployment is designed for one WhatsApp account

The Compose file describes a single bridge container holding one WhatsApp connection and one SQLite store. The MCP service gets one token environment variable in the deployment configuration shown and mounts the shared store read-only. This is a valid private single-owner layout, but not yet a multi-tenant deployment topology.

Source: `docker-compose.yml`.

## Phase 1 risk register update

| ID | Risk | Evidence status | Required resolution |
|---|---|---|---|
| P1-01 | Multiple tokens share one WhatsApp identity/data set | Confirmed by config/resource layout | Trusted principal + account registry + account-scoped service handles |
| P1-02 | Missing tool-scope metadata can bypass checks | Confirmed code path; full mapping coverage not yet measured | Deny by default; enumerate all registered tools in a test |
| P1-03 | Audit logs attribute every caller to `owner` | Confirmed | Derive identity from authenticated context |
| P1-04 | Message DB is shared and keyed by chat/message identifiers | Confirmed | Separate per-account stores; no cross-account fallback |
| P1-05 | Scheduler/drafts and identity cache are process/global-store scoped | Confirmed | Account-bound rows/paths/cache keys and per-account worker dispatch |
| P1-06 | Media root and signed URL authorization are not explicitly account-bound in the inspected setup | Confirmed as an isolation requirement; all claim paths still need trace | Bind account to signed claims and verify before file access |
| P1-07 | Response cache may reuse account-dependent results across callers | Unresolved dependency behavior | Verify locked middleware cache key; add isolation test or disable |
| P1-08 | Full Go session lifecycle is not yet traced end-to-end | Open | Inspect client construction, event handlers, QR/logout/reconnect, shutdown and cancellation |
| P1-09 | Baseline Python/Go commands have not been executed | Open | Run documented CI-equivalent commands in a real checkout and retain logs |
| P1-10 | Production backup/volume restore path is unverified | Open | Confirm deployment volume layout and rehearse backup/restore before migration |

## What is still required to exit Phase 1

1. Trace the complete Go client lifecycle from construction through QR/pairing, event-handler registration, reconnect/logout and graceful shutdown.
2. Enumerate every registered tool against `TOOL_SCOPES`; turn missing policies into explicit denials.
3. Complete the persistent/transient state inventory (all databases, media, signed links, caches, scheduled tasks, transcription, short URLs, temp paths, audit).
4. Obtain real baseline output for Python lint/tests and Go vet/staticcheck/tests/build from the repository's documented CI commands.
5. Verify deployment volumes and rehearse a cold backup and restore with the existing account's data.
6. Update the architecture's Phase 1 exit status only when the above evidence exists.

## Phase 1 conclusion

Source inspection strengthens the original risk assessment and identifies concrete authorization and audit fail-open/attribution concerns. **Phase 1 remains open.** No runtime code or database was changed, no tests were executed, and no migration is approved by this document.
