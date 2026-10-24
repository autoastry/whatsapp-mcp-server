# Multi-user Platform Upgrade — Phase 0 Repository Audit

Date: 2026-10-23
Repository: https://github.com/autoastry/whatsapp-mcp-server
Audited ref: `main` (default branch)
Audit branch: `feature/multi-user-platform-audit`

## Scope and evidence

This is a read-only architecture audit of the repository's published files. No application code was changed in Phase 0. The GitHub connector can inspect repository contents and create branch/file changes, but it does not provide a shell in which to run `pytest`, `go test`, `go vet`, `ruff`, Docker builds, or the local WhatsApp integration. Consequently, baseline tests are **not executed** in this audit and must not be treated as passing.

Inspected:
- Root directory listing, `README.md`, and repository metadata.
- `whatsapp-bridge/go.mod`, `whatsapp-bridge/main.go` (opening section), `whatsapp-bridge/main_test.go`, `whatsapp-bridge/unpaired_and_tx_test.go`.
- `whatsapp-mcp-server/server.py` (opening section), `whatsapp-mcp-server/pyproject.toml`, and the presence of `tools.py`, `whatsapp.py`, `toolsearch.py`, `observability.py`, `scheduling.py`, `identity.py`, and related modules.
- `tests/README.md` and `.github/workflows/ci.yml`.

## Current architecture observed

- **Go bridge:** `whatsapp-bridge/main.go` is a large single-file service (~199 KB in GitHub metadata). It uses `go.mau.fi/whatsmeow` at pinned pseudo-version `v0.0.0-20260622185415-5f04eac6dbbb`, `sqlstore`, SQLite, and an HTTP API. It initializes a message store at the relative path `store/messages.db`; the opening code shows global/shared store assumptions and SQLite is configured with a single open connection for writes.
- **Python MCP:** `whatsapp-mcp-server/server.py` uses FastMCP and a `StaticTokenVerifier`. Authentication is explicitly described in the file as having only one principal in the classic path. It supports a JSON list of bearer tokens with per-token client IDs/scopes, plus a legacy single-token fallback. That is useful client-level authorization, but does not by itself establish platform users or map each token to an independently owned WhatsApp session.
- **Tools and storage:** existing tools are registered from `tools.py`, with query logic in `whatsapp.py`; the README describes the bridge as holding one WhatsApp session and writing a shared SQLite message database that Python reads. This is a major multi-tenant boundary: simply adding more MCP tokens would not isolate WhatsApp sessions, messages, media, scheduled jobs, or event streams.
- **Current dependency strategy:** Python uses `pyproject.toml` plus `uv.lock`, requires Python >=3.12, and uses FastMCP; Go dependencies are pinned in `go.mod`/`go.sum`. Do not replace either runtime.
- **Existing verification:** CI defines Python lint, locked dependency install, pytest; Go vet, staticcheck, go test, and build with `sqlite_fts5`; and a Gitleaks history scan. `tests/README.md` documents an offline unit suite and separate live-session smoke/evaluation/transcription suites. These are declared workflows, not test results for this audit.

## Risks and gaps to verify in a local checkout

1. **Single-session assumption:** confirm all client creation, event handlers, QR pairing, logout and reconnect paths in the remainder of `main.go`; introduce an explicit session manager rather than adding a second mutable global client.
2. **Shared message store:** one `store/messages.db` and JID-keyed chat/message tables can mix users if multiple accounts write into the same store without a strict account partition. Evaluate separate per-account stores versus an account ID column and migration.
3. **Trusted identity:** current bearer tokens identify configured clients/scopes, not a full user/account ownership model. Every tool call, including `call_tool` meta-dispatch, must receive identity from verified transport context and enforce account ownership at the dispatch boundary.
4. **Cross-module state:** audit scheduling/drafts/chat notes, media directories and signed URLs, transcription worker, caches, event ingestion, identity resolution, and audit records for globally shared state.
5. **Internal bridge trust:** preserve private network exposure, but authenticate internal bridge requests as well; network isolation alone is not a sufficient authorization boundary.
6. **Session secret handling:** inspect whatsmeow SQL store encryption support and DB schema before selecting encryption/migration strategy. Never invent a new session format or log pairing material.
7. **Reliability and duplicate effects:** assess send paths and event handlers before retrying mutating requests; exactly-once delivery cannot be promised without a reliable upstream idempotency primitive.
8. **Existing user work:** the audit branch is based on `main`; this connector cannot inspect an uncommitted local working tree. Before implementation from a local checkout, preserve any user changes.

## Recommended phased implementation

### Phase 1 — Design
- Define trusted principal identity and token-to-user/client mapping; fail closed when identity is absent.
- Define account ownership and lifecycle state machine, with an internal opaque account ID independent of phone number.
- Decide storage isolation after tracing every DB consumer. Prefer the smallest compatible change; avoid PostgreSQL/Redis until concrete transaction, concurrency, or scale needs justify them.
- Define authenticated internal bridge API, per-tool account selection, audit events, and migration/rollback.

### Phase 2 — Go session manager
- Wrap whatsmeow client/device/store behind interfaces; own one client lifecycle per account.
- Ensure QR pairing is initiated only for an authorized owner and pairing secrets never enter ordinary tool output or logs.
- Add independent event routing, lifecycle locks, context cancellation, graceful shutdown, and reconnect tests using fakes.

### Phase 3 — Persistence and ownership
- Add versioned migrations and stable account ownership records.
- Partition messages, contacts/chats, media, scheduling, transcription and deduplication by account, or isolate them in separate per-account stores with explicit mapping.
- Add cross-account negative tests and migration rollback/failure tests.

### Phase 4 — Python MCP integration
- Keep existing tool names and schemas where feasible.
- Propagate verified principal context into all tool dispatch, including `call_tool`; reject arbitrary account IDs unless ownership is checked against trusted identity.
- Authenticate Python-to-Go requests and return safe structured errors.

### Phase 5 — Messaging reliability
- Add bounded timeouts/retries only where safe, durable jobs only if current storage semantics require them, event deduplication, and clear ambiguous-send outcomes.
- Prove that one account's failure does not block another account.

### Phase 6 — Security/regression tests
- Test missing identity, invalid token, cross-user account IDs, QR authorization, secret redaction, concurrent account operations, revocation, event deduplication, and legacy single-user behavior.

### Phase 7 — Deployment/docs
- Document config, data migration and rollback, backup/restore, internal network/auth boundaries, health checks, pairing caveats (unofficial WhatsApp Web integration where applicable), and troubleshooting.

### Phase 8 — Final verification
- Run CI-equivalent commands in a real checkout; report pass/fail/skip separately. Live WhatsApp tests must be explicitly distinguished from mocked tests.

## Baseline commands to run in a checkout

From repository root:
```bash
uv sync --locked --project whatsapp-mcp-server
uv run --locked --project whatsapp-mcp-server pytest
uvx ruff@0.8 check .
(cd whatsapp-bridge && go vet -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go run honnef.co/go/tools/cmd/staticcheck@2025.1 -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go test -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go build -tags sqlite_fts5 -trimpath -ldflags="-s -w" -o /dev/null .)
```

## Phase 0 result

**Audit completed with limitations.** The repo already has substantial auth, scope enforcement, audit logging, deployment controls and tests for a single-owner/single-session service. The upgrade must extend these controls to a true user → owned accounts → independent WhatsApp sessions model, not replace the project or mistake multi-token MCP access for multi-user session isolation.

**Next exact step:** Phase 1 — trace the complete client/session and all shared-data paths in a local checkout, then write the ownership and service-boundary design before modifying application code. Run the baseline commands above and attach their actual output to the implementation record.
