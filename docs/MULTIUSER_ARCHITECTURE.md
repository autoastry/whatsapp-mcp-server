# Multi-user Platform Architecture — Phase 1 Design

Date: 2026-10-23
Repository: https://github.com/autoastry/whatsapp-mcp-server
Target branch: feature/multi-user-platform-audit
Status: design proposal; Phase 1 remains open; focused authorization hardening is now in the feature branch

## 1. Scope and evidence boundary

This document defines the intended ownership and service boundaries before implementation. It is based on files inspected through GitHub, including whatsapp-bridge/main.go, whatsapp-mcp-server/server.py, observability.py, whatsapp.py, tools.py, toolsearch.py, scheduling.py, media.py, identity.py, CI, tests, and deployment configuration.

Evidence update (2026-10-23): the complete Go singleton lifecycle and registered-tool policy coverage have now been traced through GitHub source. A deny-by-default policy change and focused tests are committed on the feature branch. CI run #6 first failed registry coverage because `set_chat_note` was missing; that entry was added, and post-fix CI run #14 passed on commit `857634eec986500d6c4760669f241a678c6c1b28`. Existing CI run #2 passed the documented Python/Go baseline commands on the pre-hardening source revision. The GitHub integration still provides no local shell or Docker daemon, so no local command execution or safe-volume backup/restore rehearsal was possible. Post-fix CI run #14 (39941240084) passed Python lint/tests, Go vet/staticcheck/tests/build, and Gitleaks on the current source revision. The choices below remain target architecture only; no storage migration or second account has been started.

## 2. Required security invariants

1. **Verified identity only:** the principal comes from successful bearer-token verification and trusted server-side token metadata. Never accept principal_id, owner_id, or client_id supplied as tool arguments or request JSON as proof of identity.
2. **Fail closed:** missing/unknown principal, missing account selection, revoked credential, unavailable ownership registry, or invalid account state rejects the operation before data access or bridge side effects.
3. **Ownership at every boundary:** every account lookup is account_id + principal_id, not account ID alone. The Python dispatcher and Go bridge independently enforce the account boundary.
4. **Opaque stable account IDs:** use generated UUIDs (or equivalent opaque IDs); phone number/JID is mutable provider data and is never an ownership key.
5. **No cross-account fallbacks:** an unavailable account DB, session, media file, or scheduler must never cause a request to fall back to another account's data.
6. **Independent lifecycles:** pairing, reconnect, logout, deletion, and event handlers are scoped to one account. A failure for account A must not stop account B.
7. **Secrets stay secret:** never log bearer tokens, bridge shared secrets, QR payloads, pairing codes, session keys, message bodies, media keys, or signed media URLs. Audit records use identifiers, outcome, duration, tool/action name, and a one-way argument fingerprint where useful.
8. **Compatibility is explicit:** legacy single-token mode maps to one configured legacy principal and one adopted account; it must not implicitly grant ownership of newly created accounts.

## 3. Identity model and authorization

### Principal

A principal is a stable platform owner identity, independent of the token used by an MCP client. Token configuration should resolve to server-side metadata:

- token: credential verified by the transport (never returned to tools or logged)
- principal_id: stable owner identifier
- client_id: client/application label for audit and revocation
- scopes: least-privilege permissions
- enabled: whether the credential is accepted

The current WHATSAPP_MCP_TOKENS_JSON entries have client_id and scopes but no first-class owner field. Add an explicit principal mapping. For backward compatibility, entries without principal_id may map to client_id only if documented and validated as unique; the legacy WHATSAPP_MCP_TOKEN maps to the fixed legacy principal owner. Duplicate token entries, blank IDs, unknown scopes, malformed JSON, or conflicting mappings should fail startup rather than silently overwrite registry entries.

Auth middleware must create trusted request context with principal/client/scopes after token verification. Tool handlers and call_tool use that context. call_tool must retain the existing scope re-check because it dispatches inside the process and bypasses the outer per-tool middleware chain.

### Account authorization

An account operation resolves:
1. verified principal from request context;
2. requested account ID, or a server-selected legacy default only in explicit compatibility mode;
3. account registry row where account_id = requested ID AND owner_principal_id = verified principal;
4. account state allowed for the requested operation;
5. per-account service/session/store handle.

Client-supplied account IDs are selectors, not authorization. A foreign account ID must return a generic not-found/forbidden response that does not disclose whether another principal owns it. QR creation, logout, delete, and destructive WhatsApp operations require appropriate scopes and ownership checks.

## 4. Account lifecycle

Persist an account lifecycle with these states:

- provisioning: registry record created; storage not yet ready.
- pairing: QR/linking operation active for this account.
- connected: WhatsApp session is authenticated and usable.
- disconnected: not connected, but session may be restorable.
- reconnecting: reconnect attempt in progress.
- logged_out: provider session invalidated; pairing is required again.
- failed: unrecoverable setup/runtime error; safe diagnostic metadata retained.
- deleting: explicit owner-requested teardown in progress.
- deleted: tombstone retained to prevent accidental ID reuse and support audit.

Allowed transitions must be enforced in a single session-manager API, not by arbitrary HTTP handlers. QR payloads are delivered only to the authorized owner through a short-lived pairing response and are never included in logs or persistent audit events. QR requests must not reveal another account's QR. Repeated QR requests are serialized per account; cancellation/timeout cleans up pairing resources. Reconnects use bounded backoff with jitter and per-account cancellation. Deletion is explicit, idempotent at the control-plane level, and must never delete another account's storage.

## 5. Persistence and storage decision

### Target decision: control-plane registry + isolated per-account stores

Use a small local SQLite control-plane database for account metadata, ownership, lifecycle state, schema version, and audit metadata. Keep WhatsApp session/device state and message history in separate per-account storage roots, rather than adding an account column to every existing table.

Suggested layout (names are illustrative and must be centralized in path helpers):

    store/
      platform.db
      accounts/
        <opaque-account-id>/
          whatsapp.db
          messages.db
          media/
          scheduler.db

Rationale: current Go and Python code has many SQL queries against tables keyed by JID/message ID and currently opens one shared store/messages.db. Python also uses a module-level DB path and thread-local read-only SQLite connection. Per-account DB files preserve existing query shapes and make accidental cross-account SQL joins much harder than a global shared DB with ad-hoc predicates. The cost is explicit account-scoped connection/path selection, backup/restore orchestration, and file lifecycle management. Do not add PostgreSQL or Redis without a demonstrated requirement.

This is a target, not a claim that all storage is already isolated. Before implementing it, trace and classify every persistent or cached state path, including whatsmeow SQL store, message DB, identity resolver cache, Python connection cache, response cache, media/signed URLs, scheduled sends/drafts/chat notes, transcription jobs/results, deduplication state, and audit/metrics. Any shared cache must include account identity in its key or be disabled for account-dependent responses. Signed media URLs must bind to the account and authorize against caller/expiry before reading a file.

### Minimal registry schema

The exact SQL is to be added with versioned migrations, but the logical records are:

- schema_migrations(version PRIMARY KEY, applied_at)
- accounts(account_id PRIMARY KEY, owner_principal_id NOT NULL, display_name, provider_jid NULL, state NOT NULL, created_at, updated_at, last_error_code NULL, deleted_at NULL)
- account_events(event_id PRIMARY KEY, account_id, actor_principal_id, action, outcome, occurred_at, metadata_json)

Do not store bearer tokens, QR payloads, or session keys in these rows. Provider phone/JID may be stored as account metadata after pairing but cannot replace account_id. Registry constraints/indexes must support ownership lookups and lifecycle queries. Audit metadata must be allowlisted and redacted.

## 6. Go bridge service boundary

Introduce an explicit session manager that owns active sessions keyed by opaque account ID, protected by a concurrency-safe registry. Each session bundles the whatsmeow client and device-store handle, account-scoped message store, lifecycle state and per-account mutex/context/cancel, event handler registration and cleanup, and account-scoped path configuration.

HTTP handlers must stop reaching directly into process-global client and messageStore. They resolve an authorized account through a shared request helper and call account-scoped service methods. All endpoints that touch a WhatsApp client or message store require an account selector and ownership authorization, except a narrowly allowlisted health/readiness endpoint that exposes no account data.

The internal bridge API is private network traffic today, but network isolation alone is insufficient. Require service-to-service authentication and validate it before parsing account operations. Python sends an account ID only after checking ownership. Go independently checks that the account exists, is owned by the authenticated principal, and is in an allowed state. Do not trust a bare X-Account-ID header as authorization. Prefer a short-lived signed internal identity assertion or a bridge-side ownership lookup keyed by a server-authenticated principal. Missing bridge auth must fail closed in production.

The bridge must not return a QR, contact list, message, or provider identity for a request lacking valid internal authentication and account authorization. Use stable structured error codes while avoiding detailed cross-tenant existence leaks.

## 7. Python MCP boundary and tool compatibility

Keep FastMCP and existing tool names/schemas wherever feasible. Add a common account resolver and thread selected account context through all functions that access SQLite, the bridge, media, schedules, identity, and transcription. Avoid a global mutable current-account value: concurrent MCP requests could race and cross accounts. Use explicit arguments/context objects passed down to service functions; if a ContextVar is used, scope it to one request and test concurrent interleavings.

The account selector should be optional only for the legacy principal with exactly one configured/adopted default account. All other callers must select an owned account or use a documented server-side default that is unambiguous. Account management tools expose safe metadata (account ID, label, lifecycle state, timestamps), never credentials or pairing secrets.

Every directly registered tool and every tool dispatched via find_tool/call_tool must share the same principal/account authorization path. Do not depend solely on the current per-tool scope middleware. Unknown tools, missing identity, invalid account IDs, stale/deleted accounts, malformed payloads, and scope failures must be rejected before invoking a side-effecting function.

Existing response caching must be reviewed: the cache key must include principal and account for account-dependent reads, or those tools must be excluded until proven safe. Metrics labels must avoid high-cardinality account IDs and PII. Audit events should include stable opaque principal/account IDs and outcome, but not raw message text or secrets.

## 8. Migration, backup, and rollback

1. Take a cold, consistent backup of the existing WhatsApp store, message DB, media, scheduling DB, and audit files before any migration.
2. Detect whether legacy stores are empty, partially initialized, or populated. Do not silently create a second owner account if the legacy store has meaningful data.
3. Create one legacy account record for the configured legacy principal and map existing session/data to it. The legacy account ID must be stable and recorded in the registry.
4. Prefer an atomic filesystem move when stores are on the same filesystem and the service is stopped. Otherwise copy to a staging directory, verify SQLite integrity and file checksums, fsync, then atomically rename into place.
5. Preserve original files until post-migration validation succeeds. Do not rewrite whatsmeow's internal schema or fabricate session rows.
6. On any failure, keep the original store authoritative, restore previous path configuration, and mark migration failed without partially exposing the account.
7. Provide a tested rollback procedure. Rollback restores the recorded pre-migration snapshot and stops writes first.
8. Never automatically merge data from multiple legacy stores or infer ownership from phone numbers/JIDs.

Migration must be versioned, idempotent, integrity-checked, and tested with empty, populated, partially migrated, corrupt, and interrupted fixtures. The actual migration mechanism is a Phase 3 implementation task after the data-path inventory.

## 9. Reliability and delivery semantics

WhatsApp sends are external side effects. A timeout after sending is ambiguous; do not blindly retry a send unless the protocol/request has a reliable idempotency mechanism. Return a distinct outcome_unknown/ambiguous result and record a correlation ID so an operator can reconcile it. Read-only operations can use bounded timeouts and safe retries. Event handlers should deduplicate by stable provider event/message identity scoped to account. Backoff and worker limits are per account; a stuck account must not block another account. Scheduled jobs must persist the account ID and verify ownership/lifecycle at dispatch time, not merely at schedule creation.

## 10. Test plan and acceptance gates

Offline CI must remain independent of a live WhatsApp account. Add deterministic fakes for session/device stores and HTTP requests. Required suites:

- token/principal mapping, missing identity, invalid/revoked tokens, duplicate/conflicting mappings, unknown scope;
- account ownership matrix: owner A can access A, owner B can access B, neither can access the other's account; guessed IDs reveal no foreign metadata;
- account lifecycle transitions, concurrent QR requests, QR expiry/cancellation, reconnect/restore, logout and delete isolation;
- simultaneous reads/writes for two accounts, cache-key isolation, media URL isolation, scheduling/draft isolation, identity-cache isolation;
- Python call_tool authorization parity with direct tool invocation;
- internal bridge authentication failure, expired assertion rejection if signed assertions are used, missing account and wrong owner;
- event deduplication and reconnect idempotence;
- send timeout after simulated remote acceptance returns ambiguous outcome and is not auto-retried;
- migration fixtures (empty, populated, corrupt, interrupted), SQLite integrity checks, backup/restore and rollback;
- existing single-user regression, Go tests, Python tests, lint, staticcheck, build, and Go↔Python contract tests.

No acceptance criterion is satisfied merely because a test file exists. Report actual commands and outcomes separately from unrun/live-only tests.

## 11. Sequencing and phase exit criteria

- Phase 1 (this document): establish target ownership, lifecycle, storage boundary, bridge trust, migration and acceptance gates. The Go singleton lifecycle has been source-traced and baseline CI passed on the pre-hardening revision, but Phase 1 exit remains blocked on a local transient-state exercise, cache-isolation proof or a decision to disable account-dependent caching before multi-user enablement, and a cold backup/restore rehearsal on a disposable copy. CI run #14 is green, but no local commands were executed in this environment.
- Phase 2: implement the Go session manager behind interfaces and lifecycle fakes; do not migrate storage yet.
- Phase 3: implement registry migrations and account-scoped storage with verified legacy adoption/rollback.
- Phase 4: thread trusted identity/account context through Python tools, call_tool, media, scheduler, and bridge client.
- Phase 5: implement bounded retries, deduplication, per-account backpressure, and ambiguous-send semantics.
- Phase 6: close security and regression coverage.
- Phase 7: document production deployment, configuration, backup/restore, security boundary and operations.
- Phase 8: run CI-equivalent commands in a real checkout and report results with evidence.

### Phase 1 exit status

**Design drafted; implementation gate not yet passed.** Required remaining evidence before Phase 2/3 code changes:
- complete local trace of Go client construction, QR/login/logout, reconnect, event handler registration and shutdown;
- full inventory of all DB/cache/filesystem/worker state paths;
- baseline outputs for the documented Python and Go commands;
- confirmation of current production volume mounts and a verified backup/restore path.

The current GitHub-only environment cannot produce local execution or volume-restore evidence. CI run #14 is green for the repository's configured checks, but independent formatting checks, live-session tests, and backup/restore remain unexecuted. Do not describe migration as safe until those gates are completed.


## Phase 1 evidence update — 2026-10-23

- Source trace now covers the single Go client/device-store construction, QR path, event handler, whatsmeow auto-reconnect behavior, absence of a logout API, fixed singleton REST handlers, and incomplete graceful shutdown. See `docs/MULTIUSER_PHASE1_TRACE.md`.
- Mechanical source comparison found 96 registered regular tools plus `find_tool` and `call_tool`. The scope table has been extended and middleware/dispatcher checks now deny missing policy and missing caller context instead of allowing. Automated coverage tests were added. CI run #6 reported 1 failed and 103 passed because `set_chat_note` was missing from the policy map; the missing entry was committed afterward, and CI run #14 passed on the corrected source revision.
- Baseline CI run #2 succeeded for Python lint/tests, Go vet/staticcheck/tests/build, and Gitleaks on the pre-hardening revision. This is CI evidence, not a local run from the connector environment. Ruff format and gofmt checks are not currently part of authoritative CI.
- Current compose volume mapping is confirmed from source: `wa-store` bridge read/write and MCP read-only; `wa-audit` stores audit, scheduling, transcription and short-link DB paths; `wa-media` stores decrypted media cache. These are configuration observations, not inspection of the live volume contents.
- No live data was opened, copied, modified, migrated, or restored. Backup/restore remains unverified because the environment has no Docker daemon or production host access.

**Phase 1 is not closed.** No account-scoped storage migration or multi-session runtime changes are authorized by this design addendum. The smallest first implementation after the gates close remains a Go session-manager interface and fake lifecycle tests, behind a compatibility-preserving seam, with a revert-only rollback and no live database changes.


## Updated verification status — 2026-10-23

Post-fix CI run #14 (39941240084) completed successfully on commit `857634eec986500d6c4760669f241a678c6c1b28`. The Python lint, locked dependency install, pytest, Go vet, staticcheck, Go test, Go build, and Gitleaks jobs all succeeded. This is CI evidence, not an independent local run. The response cache configuration is still not suitable evidence of tenant partitioning: the source comment says cache results are keyed on tool arguments and does not establish inclusion of trusted principal/account. Disable account-dependent caching before enabling multiple principals unless the locked middleware implementation and cross-principal test prove isolation.


## Phase 1 follow-up — 2026-10-23

The previous section's cache warning has been acted on: commit `90185b8512a124b3248e9b32bef800bf1c3e10cd` removes `ResponseCachingMiddleware` from `whatsapp-mcp-server/server.py` until the locked dependency's cache key can be proven to include authenticated principal and authorized account identity. This is a mitigation, not proof of multi-user isolation. A regression test and any future cache design must verify that no account-dependent result can cross principals.

The latest completed CI run before that change was run #16 (`39941613651`) on `a053f4a758f8d10d1d3ac0d128a1cd0920904e87`; Python lint/tests, Go vet/staticcheck/tests/build, and Gitleaks all succeeded on that revision. CI for the cache-removal commit and following documentation commits must be checked separately. No local test commands were executed because no repository checkout shell was available. No Docker volumes or live data were accessed; cold backup/restore is still unverified.

Phase 1 remains open. Required blockers include CI on the current branch head, caller-aware audit attribution, negative authorization/configuration tests, account ownership and cross-account denial tests, transient-worker cleanup verification, and a cold restore rehearsal against disposable data. Do not pair another WhatsApp account or begin storage migration until these gates have evidence.
