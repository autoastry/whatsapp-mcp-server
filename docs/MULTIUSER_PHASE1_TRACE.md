# Phase 1 Source Trace — Multi-user WhatsApp MCP

Date: 2026-10-23
Branch: `feature/multi-user-platform-audit`
Status: Phase 1 remains open; follow-up source trace and focused authorization hardening recorded below

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
| P1-02 | Missing tool-scope metadata can bypass checks | Original fail-open confirmed; explicit policies added for all 98 registered + meta tools by source comparison; CI run #6 found one missing policy (`set_chat_note`); that entry was added after the failing run; verification of the follow-up revision is still pending | Keep fail-closed behavior and require registry-vs-policy coverage test in CI |
| P1-03 | Audit logs attribute every caller to `owner` | Confirmed | Derive identity from authenticated context |
| P1-04 | Message DB is shared and keyed by chat/message identifiers | Confirmed | Separate per-account stores; no cross-account fallback |
| P1-05 | Scheduler/drafts and identity cache are process/global-store scoped | Confirmed | Account-bound rows/paths/cache keys and per-account worker dispatch |
| P1-06 | Media root and signed URL authorization are not explicitly account-bound in the inspected setup | Confirmed as an isolation requirement; all claim paths still need trace | Bind account to signed claims and verify before file access |
| P1-07 | Response cache may reuse account-dependent results across callers | Unresolved dependency behavior | Verify locked middleware cache key; add isolation test or disable |
| P1-08 | Full Go session lifecycle is not yet traced end-to-end | Source trace completed; lifecycle is still single-client and graceful shutdown incomplete | Implement per-account manager with cancellation, handler cleanup, server shutdown and lifecycle tests |
| P1-09 | Baseline Python/Go commands have not been executed | Existing CI run #2 succeeded on pre-hardening source commit `6c1ba7a`; new hardening CI run #6 queued at time of update | Review the post-change CI run and retain job evidence; format checks remain outside current CI |
| P1-10 | Production backup/volume restore path is unverified | Still open; Compose mounts are source-confirmed but no backup/restore was executed | See updated risk register below |

## What is still required to exit Phase 1

1. Trace the complete Go client lifecycle from construction through QR/pairing, event-handler registration, reconnect/logout and graceful shutdown.
2. Enumerate every registered tool against `TOOL_SCOPES`; turn missing policies into explicit denials.
3. Complete the persistent/transient state inventory (all databases, media, signed links, caches, scheduled tasks, transcription, short URLs, temp paths, audit).
4. Obtain real baseline output for Python lint/tests and Go vet/staticcheck/tests/build from the repository's documented CI commands.
5. Verify deployment volumes and rehearse a cold backup and restore with the existing account's data.
6. Update the architecture's Phase 1 exit status only when the above evidence exists.

## Phase 1 conclusion

At the time of this initial trace, no runtime code or database had been changed and no tests had been executed. The follow-up below records the later focused authorization code/test changes and CI evidence. **Phase 1 remains open**, and no storage migration is approved by this document.


---

## Follow-up evidence — Go lifecycle, tool coverage, state inventory, and verification

Date: 2026-10-23
Branch: `feature/multi-user-platform-audit`

This follow-up updates the source-only findings above. It does not close Phase 1. The GitHub connector can inspect committed source and CI metadata, but it does not provide a local shell, a Docker daemon, a live deployment, or access to the user's production volumes.

### 8. Go WhatsApp client lifecycle traced from `main.go`

Relevant source locations in `whatsapp-bridge/main.go` (line numbers at inspected revision):

- `NewMessageStore` at about line 69 opens the fixed relative path `store/messages.db`; it creates one store with chat/message, FTS and derived-state tables.
- `main` at about lines 4760-4810 opens one whatsmeow `sqlstore` at `file:store/whatsapp.db?_foreign_keys=on`, calls `GetFirstDevice`, creates one device if absent, and constructs one `whatsmeow.NewClient(deviceStore, logger)`. There is no account registry or client map.
- One `MessageStore` is created and shared with the client, HTTP handlers and event handler. `startWALCheckpoints` and `startMediaReaper` are started under a single `context.WithCancel(context.Background())`.
- One global event handler is registered with `client.AddEventHandler` around line 4863. It closes over the single client and message store. It routes `events.Message`, `HistorySync`, `Receipt`, `Pin`, `Picture`, poll/reaction/group/chat-state and connection events into that one store. History sync starts a detached goroutine and uses `context.Background()`; it is not cancelled/joined during shutdown. No handler ID is retained for removal.
- If `client.Store.ID == nil`, `GetQRChannel(context.Background())` is called before `Connect()`; QR events are printed to stdout and written to the fixed `store/qr.png` path. Pairing waits up to three minutes, then returns from `main`. QR context is not tied to process shutdown. If an existing device ID is present, the same client is connected without pairing.
- After a fixed two-second sleep, `IsConnected()` is checked, then `startRESTServer(client, messageStore, port)` registers handlers that close over those same singleton objects.
- `startRESTServer` creates an `http.Server` and starts `ListenAndServe` in a goroutine, but returns no server handle. The main goroutine later blocks on SIGINT/SIGTERM and calls `client.Disconnect()`. It does not call `http.Server.Shutdown`, `signal.Stop`, wait for handlers/background work, explicitly cancel the checkpoint/reaper context before returning, or close the QR channel. Deferred store close/cancel run on return, but ordering does not constitute a full graceful-shutdown protocol.
- The inspected source contains no call to `client.Logout()`; the current lifecycle handles the `events.LoggedOut` notification by logging that a new QR is needed. It does not expose a per-account logout lifecycle operation.

Reconnect behavior: the bridge logs `events.Disconnected` with a note that whatsmeow auto-reconnects. The current process delegates reconnect to the library for the one client; there is no explicit per-account retry/backoff/cancellation manager or state machine. `Connect()` errors during startup cause process startup to return.

Single-client assumptions to remove in Phase 2:
1. `main`'s single `container`, first-device selection, `client`, `messageStore`, and fixed `store/whatsapp.db` / `store/messages.db` paths.
2. Every HTTP handler created by `startRESTServer(client, messageStore, ...)` that closes over global handles.
3. The event handler's closures, history-sync goroutine, media-retry channels and any caches keyed only by message ID/JID.
4. Fixed QR output path and terminal QR rendering; pairing output must be scoped to the authorized account and must not be written to shared logs or a common path.
5. One shared media reaper and WAL-checkpoint worker; maintenance workers must be tied to a session/store context and stopped before its DB is closed.
6. Process shutdown: retain server/handler handles, cancel pairing and background contexts, stop HTTP intake, wait for in-flight handlers/workers, disconnect each active client, remove event handlers, and close each account store without affecting other sessions.

The HTTP bridge currently relies on private Docker-network reachability rather than request-level service authentication. It has no account selector/ownership resolver at the Go handler boundary. Adding an account ID header alone would not establish authorization.

### 9. Mechanical MCP tool-policy comparison and focused hardening

The registered source library consists of 86 tools from `tools.py` and 10 from `scheduling.py`; minimal mode captures the full registry before pruning, then adds `find_tool` and `call_tool`. That gives **96 regular tools + 2 meta-tools = 98 callable names** in the inspected registry. This count was compared against `observability.TOOL_SCOPES` using the actual decorator names, including the scheduling tools whose Python wrapper function names differ from their public tool names.

The original map covered only 55 regular tools and omitted a substantial part of the long tail, including analytics, reactions, identity, transcription helpers, labels, scheduler/draft tools, and `delete_chat`. In the original implementation:
- `AuthzMiddleware` only checked when `TOOL_SCOPES.get(tool_name)` returned a value, so missing entries passed through.
- `toolsearch._enforce_scope` caught all import exceptions and returned, and returned when a scope entry was missing.
- `call_tool` returned a structured error for unknown names, but a known tool without a mapping reached its implementation without a scope check.

Focused hardening was committed on this branch:
- Added explicit policies for the previously uncovered registered tools and for `find_tool` / `call_tool`. The meta dispatcher requires authenticated transport scope; `call_tool` still re-checks the target tool's scope.
- `AuthzMiddleware` now rejects missing policy entries, missing caller context, and failures to resolve caller context instead of calling the tool.
- `toolsearch._enforce_scope` now rejects policy-import failure, missing policy entries, and absent caller context with `ToolError`; it no longer catches-and-allows.
- Added offline tests for unknown/unmapped target denial, policy-import failure, missing auth context, and preservation of a legitimate `whatsapp:send` dispatch.
- Extended the server boot test to compare the full captured library (including pruned dynamic tools) and directly exposed meta/core tools against `TOOL_SCOPES`.

Evidence boundary: these changes have been committed to the feature branch but the post-change CI run was queued at the latest check. Do not call the new tests passing until the run completes. The complete registry-coverage test is the mechanical guard against future tools being added without a policy.

### 10. Complete account-dependent data/state inventory

| Resource | Current source / ownership boundary | Read/write and lifecycle evidence | Multi-user requirement |
|---|---|---|---|
| whatsmeow device/session DB | Go `sqlstore`, fixed `store/whatsapp.db`; first device loaded from one container | Bridge reads/writes session, contacts and LID map; persists across restart in `wa-store` | One DB/device store per opaque account ID; never select by phone/JID |
| Message/chat DB and FTS | Go `NewMessageStore`, fixed `store/messages.db`; Python reads `MESSAGES_DB_PATH` in read-only mode | Go writes messages, chats, reactions, polls/votes, participants, profile picture cache, labels/chat state and receipts; Python queries via per-thread cached SQLite connections | Separate per-account DB roots and connection caches; close/evict connections on account teardown |
| Whatsmeow contacts / LID mappings | Tables in `whatsapp.db`; Python `identity.py` loads module-global maps once from `WHATSMEOW_DB_PATH` | Read-only load of `whatsmeow_lid_map` and `whatsmeow_contacts`; a missing DB is cached as loaded/no-op | Cache key includes account; never share maps between accounts; invalidate/reload on store changes |
| Bridge client and HTTP handlers | Singleton `client` passed into `startRESTServer`; Python module-level `requests.Session` and one `WHATSAPP_API_BASE_URL` | All HTTP endpoints close over one client/store; Python session is pooled across all calls | Resolve a trusted account before request; per-account service handle and authenticated bridge assertion; no bare header trust |
| Media and decrypted cache | Go media files under `store/<chat-JID>/...`; Python `WHATSAPP_MCP_MEDIA_DIR` default `/data/media`; Docker `wa-media` volume | Bridge downloads to disk; Python fetches CDN or bridge, decrypts and caches files; bridge reaper deletes old files under JID-looking directories | Account-scoped paths; reaper per account; prevent symlink/path traversal; do not clean one account's files during another's lifecycle |
| Signed media URLs | `media.py` HMAC claims currently include message ID, chat JID, filename and expiry, not account ID; key derived from a configured bearer token (first JSON token if legacy token absent) | `/media/{token}` validates HMAC/expiry then reads the shared message DB/cache; URL is a bearer capability until expiry | Bind account ID and principal/credential context to claims; verify authorization before file lookup; rotate/revoke safely; never log URLs |
| Short media URLs | `WHATSAPP_MCP_SHORT_DB`, default `/var/log/wamcp/shorturls.db`; `wa-audit` volume | SQLite rows map random code to message ID/chat JID/filename/expiry; `/s/{code}` and `/p/{code}` are capability links | Store account ID in claims/rows; account-scoped DB or partitioned rows; expire/revoke and backup deliberately |
| Transcription | `transcription.py`; `TRANSCRIPTS_DB=/var/log/wamcp/transcripts.db`; fallback `/tmp/wamcp-transcripts.db`; `MESSAGES_DB_PATH` points at shared messages DB | `transcripts` primary key is `(message_id, chat_jid)`; `key_state` stores SHA-derived Speechmatics key exhaustion state. `_pending` queries the shared messages DB and builds `handled` using only `message_id`, so two accounts can collide. Daemon worker polls forever, uses up to four concurrent workers, downloads/decrypts media, extracts `.aud.ogg` from videos, and writes transcript text/status/error; optional Speechmatics/Groq credentials activate it. | Account-scope transcript rows, key-state (or explicitly keep provider-key state global), handled/dedup keys, message DB selection and worker queues; per-account worker cancellation; test extraction/temp cleanup on exceptions and cancellation; do not share transcript text across accounts |
| Scheduler, drafts and chat notes | `scheduling.py` uses `WHATSAPP_MCP_SCHEDULE_DB`, fallback `/var/log/wamcp/scheduling.db` then `/tmp/wamcp-schedule.db`; Compose mounts `wa-audit` at `/var/log/wamcp` | Tables: `scheduled_sends`, `drafts`, `chat_notes`; daemon thread ticks every 30 seconds and dispatches sends through the shared bridge | Add owner/account IDs to every row; dispatch only after rechecking owner/account lifecycle; isolate workers and retry state; do not migrate without snapshot |
| Audit log | `WHATSAPP_MCP_AUDIT_LOG`, Compose `/var/log/wamcp/audit.jsonl`; fallback `/tmp/wamcp-audit.jsonl` | Middleware and dispatcher append/fsync mutating-call records; current client attribution is hard-coded to `owner`; fallback may be ephemeral | Record trusted principal/account IDs and outcome; never store message text, QR, token or signed URL; retention/backup and fallback alerting needed |
| Response cache | FastMCP `4.0.3` locked in `uv.lock`; `ResponseCachingMiddleware` caches an allowlist of read tools for 30 seconds, keyed by tool arguments per comments | No explicit principal/account cache partition is configured in `server.py`; dependency internals were not executed/verified here | Treat account-dependent cache isolation as unproven; disable response caching for account-dependent tools before multi-user enablement unless a cross-principal cache-key test proves partitioning |
| Tool retrieval/embedding cache | `toolsearch.py`: process-global index, query-vector cache; optional `WHATSAPP_MCP_EMBED_CACHE` default `/var/log/wamcp/tool_embeddings.json` | Index is tool metadata, not WhatsApp message content; shared across principals is acceptable only while it remains tool-only | Keep user/account data out of this cache; if query vectors are later personalized, partition or disable |
| Temporary media conversion | `whatsapp.py` uses `/tmp/wamcp-media` (0700) and optional `WHATSAPP_MEDIA_ROOTS`; audio conversion uses `NamedTemporaryFile(delete=False)` and cleans paths in `finally` | Conversion failure/early return paths and converter-created temporary output need local regression checks | Per-request temp directories; guaranteed cleanup; avoid shared predictable names; test cleanup on failure/cancellation |
| Go media-retry channels | Process-global map keyed by message ID (observed in event handler) | Event handler routes retry events to waiting download goroutines | Key by account + message ID; close/drain waiters on disconnect and cancellation |
| Background tasks | Go history sync goroutine uses `context.Background()`; Go checkpoint/reaper share process context; Python transcription worker and scheduler daemon thread are process-global | Shutdown currently disconnects one client but does not gracefully stop HTTP server or join all worker tasks | Per-account contexts, bounded concurrency, cancellation and shutdown ordering; account A failure must not stop account B |
| Container volumes/config | Compose: `wa-store` mounted bridge RW at `/app/store`, MCP RO at `/data/store`; `wa-audit` RW at `/var/log/wamcp`; `wa-media` RW at `/data/media`; only MCP port is host-published on loopback | `wa-store` includes `messages.db`, `whatsapp.db`, WAL/SHM files, QR PNG and chat-JID media dirs; secrets/API keys injected via env | Snapshot all volumes and env/config references before migration; never print secrets; use explicit account volume paths and separate bridge auth |

The transcription module has now been source-traced, including its SQLite schema, forever-polling daemon worker, message-ID deduplication, key exhaustion cache and video extraction temp files. Runtime cleanup under cancellation, all media-cache naming behavior, and FastMCP signed-response-cache internals still require local execution. The table records source evidence, not proof that every transient path has been exercised.

### 11. Baseline verification evidence

Authoritative CI is `.github/workflows/ci.yml`. The repository CI run **#2 / run ID `39939399999`** associated with the pre-hardening source commit `6c1ba7ab87caf1e84dd74b9275c0b515f78f3d7d` completed successfully. Job metadata shows all steps below concluded `success` (which means the command step exited zero); this is CI evidence, not a claim that commands ran in this GitHub connector process.

| Check | Exact CI command | Result on baseline commit |
|---|---|---|
| Python lint | `uvx ruff@0.8 check .` | exit 0 |
| Locked Python dependency sync | `uv sync --locked --project whatsapp-mcp-server` | exit 0 |
| Python offline tests | `uv run --locked --project whatsapp-mcp-server pytest` | exit 0 |
| Go vet | `go vet -tags sqlite_fts5 ./...` (working directory `whatsapp-bridge`) | exit 0 |
| Go staticcheck | `go run honnef.co/go/tools/cmd/staticcheck@2025.1 -tags sqlite_fts5 ./...` | exit 0 |
| Go tests | `go test -tags sqlite_fts5 ./...` | exit 0 |
| Go build | `go build -tags sqlite_fts5 -trimpath -ldflags="-s -w" -o /dev/null .` | exit 0 |
| Secret scan | `gitleaks git . --no-banner --redact --exit-code 1` after downloading pinned 8.24.3 CLI | exit 0 |

No explicit `gofmt -d` or `ruff format --check` is configured in CI; the workflow comments say Ruff formatting is deliberately not enforced. These checks have not been executed independently in this audit. CI run #6 / `39940720303` completed: Go vet/staticcheck/tests/build and Gitleaks succeeded, Python lint and locked sync succeeded, but pytest failed (1 failed, 103 passed) because the registry-coverage assertion found `set_chat_note` missing. The missing policy entry was added in commit `7a1cfec`; that run predates the fix. A successful post-fix run has not yet been observed, so the authorization change remains unverified. Live WhatsApp smoke tests, tool-search evaluation, voice harness, backup/restore, and production data checks have not been run.

Reproducible local commands from repository root:

```bash
uv sync --locked --project whatsapp-mcp-server
uvx ruff@0.8 check .
uv run --locked --project whatsapp-mcp-server pytest
(cd whatsapp-bridge && gofmt -d .)
(cd whatsapp-bridge && go vet -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go run honnef.co/go/tools/cmd/staticcheck@2025.1 -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go test -tags sqlite_fts5 ./...)
(cd whatsapp-bridge && go build -tags sqlite_fts5 -trimpath -ldflags="-s -w" -o /dev/null .)
```

### 12. Backup and restore safety gate

The Compose source confirms named volumes `wa-store`, `wa-audit`, and `wa-media`; it does **not** reveal the contents of the running production volumes or prove that an operator has a valid backup. No production volume was accessed or changed during this audit. A safe backup/restore rehearsal has **not** been executed because this environment has no Docker daemon or access to the deployed host.

Required procedure before any migration:
1. Identify the exact Compose project/volume names with `docker compose config` and `docker volume ls`; do not assume the default volume names if Compose uses a project prefix.
2. Schedule a maintenance window and stop writers cleanly: `docker compose stop mcp bridge`. Do not perform a cold-copy claim while either container is writing SQLite WAL files.
3. Create an archive of each named volume from a read-only mount using a temporary backup directory outside the volumes. Preserve all files, including `*.db-wal`, `*.db-shm`, QR/session files, media subdirectories and permissions. Store archives encrypted and access-controlled; never paste archive contents or env secrets into chat/issues.
4. Restore each archive into a **new disposable test volume** (never over the original) and inspect SQLite DBs using Python's standard `sqlite3` module: run `PRAGMA integrity_check` on `messages.db`, `whatsapp.db`, `scheduling.db`, `transcripts.db`, and `shorturls.db` where present. Missing optional DBs must be recorded, not silently substituted. Compare file lists and SHA-256 checksums for files copied byte-for-byte.
5. Start a throwaway Compose stack against only the restored test volumes and test reads/health without pairing or sending a message. Confirm the existing session remains available in the test copy; never connect two live bridge processes to the same copied session at once.
6. Record restore results, archive hashes, volume mapping, SQLite integrity output, and rollback steps. Keep originals authoritative until an independently reviewed restore succeeds.

Illustrative *test-copy-only* SQLite check (run against a restored disposable volume, never a live volume):

```bash
docker run --rm -v wa-store-restore-test:/data:ro python:3.12-slim python -c '
import pathlib, sqlite3
for p in sorted(pathlib.Path("/data").glob("*.db")):
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        result = db.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"{p.name}: {result}")
        if result != "ok":
            raise SystemExit(1)
    finally:
        db.close()
'
```

This is a runbook proposal, **not a completed backup/restore test**. A production snapshot/restore remains a hard blocker.

### Updated Phase 1 risk register

| ID | Risk | Status after this follow-up | Required closure evidence |
|---|---|---|---|
| P1-01 | Multiple tokens share one WhatsApp identity/data set | Confirmed, unresolved | Trusted principal/account registry and per-account service handles |
| P1-02 | Missing tool-scope metadata can bypass checks | Code hardened; CI run #6 failed on one missing `set_chat_note` policy, then policy was added; post-fix CI not yet observed | Green post-fix CI; coverage test enumerates all 98 names; consider middleware-level negative test |
| P1-03 | Audit identity hard-coded to owner | Confirmed, unresolved | Trusted request principal propagation and audit tests |
| P1-04 | Shared message DB and global client | Confirmed, unresolved | Per-account stores/session manager; cross-account negative tests |
| P1-05 | Scheduler/drafts/identity cache are global | Confirmed, unresolved | Account-aware schema, cache key, worker dispatch and isolation tests |
| P1-06 | Media/signed URLs not account-bound | Confirmed, unresolved | Account-bound claims and authorization-before-file-access tests |
| P1-07 | Response cache partitioning | Unproven | Disable account-dependent caching or prove trusted principal/account keying with cross-principal test |
| P1-08 | Go lifecycle assumes one client and incomplete shutdown | Fully source-traced; design unresolved | Lifecycle manager, QR cancellation, per-account event routing, server shutdown, worker join tests |
| P1-09 | Baseline verification | Baseline CI green; post-hardening run #6 failed on coverage test; fix committed afterward | Obtain green post-fix CI; add/execute formatting check and retain job evidence |
| P1-10 | Backup/restore | Unverified; no production access in this environment | Cold backup and restore into a disposable volume, integrity/checksum evidence |
| P1-11 | Transcription/transient path inventory | Transcription source and temp-file paths traced; runtime cleanup/cancellation and full media-cache behavior untested | Local exercise temp cleanup/cancellation, per-account dedup keys, and cache/worker partition tests |

### Phase 1 status and next step

**Phase 1 remains OPEN.** The source lifecycle is now traced, and a focused deny-by-default authorization change plus tests has been committed, and the first post-change CI run exposed a missing `set_chat_note` policy, which was fixed after that run; a green post-fix CI run is still required. The account-dependent data paths still need a local end-to-end trace, response-cache partitioning is unproven, and backup/restore has not been rehearsed on a safe copy.

Do not start storage migration, pair a second account, alter live session files, or close Phase 1 until those blockers have evidence. Once Phase 1 closes, the smallest safe first implementation step is a Go-only session-manager interface plus deterministic fake lifecycle tests, with no storage migration and no changes to the legacy single-session runtime path. Rollback is reverting that isolated commit; existing `store/whatsapp.db` and `store/messages.db` remain untouched.
