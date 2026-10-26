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


## Next implementation milestone proposal — identity and account ownership foundation

**Status: design only; not wired into runtime.** Phase 1 is still open, so this proposal must not be interpreted as permission to change session storage, pair another account, or migrate live data. The latest branch CI run #24 (`39942762986`) succeeded for Python lint/tests, Go vet/staticcheck/tests/build, and Gitleaks on the then-current trace commit. This does not satisfy the unexecuted backup/restore rehearsal or resolve branch-history divergence: the compare API still reports 25 commits ahead and 2 behind `main`.

### Trust model

Keep the current static-token compatibility path initially, but distinguish three separate concepts:

1. **Principal**: a stable platform identity authenticated by the MCP verifier. It must come from trusted server-side token metadata, never from a tool argument or caller-supplied header.
2. **WhatsApp account**: an opaque platform-generated account ID. It represents a linked-device session, not the WhatsApp phone number and not the bearer token.
3. **Ownership grant**: an explicit relation authorizing a principal to operate a given account, with role/state and revocation. Every account-scoped operation must resolve this grant before touching message history, media, scheduling, or the Go bridge.

The current `client_id` in token metadata is configuration-provided and audit code hard-codes `owner`; do not silently assume the FastMCP token object exposes that metadata as a trusted principal until an integration test proves the exact locked-version API. Add a small adapter at the authentication boundary and test its output from the real FastMCP context. If identity cannot be resolved, deny account-scoped access.

### Proposed registry schema (new platform-owned SQLite DB; no legacy DB migration in the first commit)

```sql
CREATE TABLE platform_principals (
  principal_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('active','disabled')),
  created_at TEXT NOT NULL
);

CREATE TABLE whatsapp_accounts (
  account_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('pending_pairing','active','disconnected','revoked','error')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE account_ownership (
  principal_id TEXT NOT NULL REFERENCES platform_principals(principal_id),
  account_id TEXT NOT NULL REFERENCES whatsapp_accounts(account_id),
  role TEXT NOT NULL CHECK (role IN ('owner','operator','reader')),
  status TEXT NOT NULL CHECK (status IN ('active','revoked')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (principal_id, account_id)
);

CREATE INDEX account_ownership_by_account
  ON account_ownership(account_id, status);
```

Use opaque random IDs, not phone numbers, JIDs, tokens, or user-controlled strings. Store token hashes/secret material separately from the ownership registry; never store bearer tokens in audit records. Apply foreign keys and transactions. The initial schema commit should only create a module and tests against a temporary SQLite path; it must not automatically open or migrate `store/whatsapp.db`, `messages.db`, or deployment volumes.

### Proposed modules and integration order

1. **Python identity/ownership module** (new): principal/account/ownership types, repository interface, SQLite implementation on an explicitly configured platform DB path, and a single `require_account_access(principal, account_id, action)` function that denies missing principal, unknown account, disabled/revoked principal, missing grant, revoked grant, and unsupported role/action.
2. **Authentication adapter** in `server.py`: derive principal exclusively from authenticated token metadata returned by the installed FastMCP version. Add a test with two distinct token identities through the real in-memory FastMCP client before relying on this field. Fail startup if multi-user mode is enabled but identity mapping is not supported.
3. **Authorization integration**: update both direct middleware and `call_tool` dispatch to use the same policy and request context. Existing scope checks remain necessary; ownership is an additional check, not a replacement.
4. **Audit attribution** in `observability.py` and `toolsearch.py`: record the authenticated principal ID and authorized account ID, never `owner` or a caller-supplied identity. Include denied attempts and outcome while redacting arguments.
5. **Go seam**: only after Phase 1 closes, define a session-manager interface around the pinned whatsmeow version. The existing singleton implementation remains the default adapter. Fake session tests should prove event routing, cancellation, lock behavior and cleanup before introducing a second client.
6. **Data routing**: after registry/identity tests are stable, thread the resolved account context into Python tool handlers, media claims, scheduler rows/workers, identity mappings, and the bridge request protocol. No request may select an account merely by passing an arbitrary account ID.

### Backward compatibility and migration

- Keep current single-account mode working behind an explicit legacy mapping (e.g. the configured legacy principal mapped to one generated account ID). Do not infer ownership solely from token scope.
- Do not automatically attach existing session files to a new principal at startup. Adoption must be an explicit operator-controlled command with a dry-run report and a verified backup.
- The first registry migration should be additive: create a new platform registry database, leaving the legacy WhatsApp device and message databases unchanged.
- For rollout, run in audit/shadow mode first: resolve the legacy account and log policy decisions without changing existing tool routing. Switch account-scoped enforcement on only after parity and denial tests pass.
- Never dual-write the same WhatsApp message or session into multiple account stores as a shortcut.

### Required tests before enabling any multi-user runtime path

- Principal extraction from actual FastMCP authentication context; missing/ambiguous identity denied.
- Principal disabled/revoked, account absent/revoked/disconnected, and grant absent/revoked denied.
- User A can access only A's account; User B can access only B's account; a shared account is accessible only if an explicit grant exists.
- Direct tool calls and `call_tool` dispatcher have identical scope + ownership outcomes.
- Audit records attribute A/B distinctly and contain no tokens, message bodies, QR payloads or signed media URLs.
- Media signed claims are account-bound and authorization is checked before filesystem access.
- Scheduler/drafts/notes and background retries re-check ownership at dispatch time.
- Legacy mode regression suite passes with no schema changes to the legacy DB.
- Run Python tests/lint, Go tests/vet/staticcheck/build, `ruff format --check`, and `gofmt -d`; live linked-device tests remain a separately authorized test-account gate.

### Rollback

Before enforcement, the new registry is additive and can be disabled by configuration; legacy session and message DBs remain untouched. Keep the legacy singleton adapter as default until the feature flag is explicitly enabled. If any post-deployment issue occurs, disable multi-user mode and revert the isolated ownership/session commits; do not delete registry rows or restore over live databases as a rollback shortcut. Restore rehearsal and recorded hashes are prerequisites for any production rollout.

**Decision:** do not begin runtime account-session implementation until the backup/restore rehearsal is evidenced and the branch/main divergence is reviewed. The next code PR should be limited to the isolated ownership registry + pure authorization policy + tests, without connecting it to message tools or the Go session lifecycle.

## 12. Isolated ownership registry implementation checkpoint (2026-10-24)

The feature branch now contains an initial, deliberately unintegrated Python control-plane registry in `whatsapp-mcp-server/account_registry.py`. It requires an explicit SQLite path, creates a separate versioned registry schema for principals, account lifecycle status, and ownership grants, and does not inspect or adopt the legacy WhatsApp stores. The first offline policy tests are in `tests/unit/test_account_registry.py`.

The policy currently denies by default on missing identity/account/action, disabled principals, absent or revoked grants, insufficient role, and incompatible account lifecycle state. Account creation atomically creates the account and its owner grant. Sharing grants are limited to reader/operator roles and require the acting principal to have manage permission; revocation also requires manage permission and cannot revoke the owner grant.

CI run #28 (GitHub Actions run 40071720977) passed on commit `fcd809b0e4358293f624a05622efd1b7747982f3`: Python lint and unit tests, Go vet/staticcheck/tests/build, and Gitleaks. This is CI evidence only; no local shell was used. The registry remains unconnected to FastMCP request identity, MCP tools, media, scheduler, or the Go bridge. It is not yet a production authorization boundary.

Remaining checks before integration:
- expand policy tests for role/state matrices, invalid inputs, persistence/reopen, and concurrent access;
- verify exact trusted principal extraction from the pinned FastMCP auth context and add a focused test;
- decide and document transaction/locking semantics for concurrent grant revocation;
- complete disposable cold backup/restore rehearsal and review branch/main divergence;
- keep Phase 1 open until all existing gates are evidenced.

## 13. Trusted principal extraction checkpoint (2026-10-24)

Added `whatsapp-mcp-server/auth_identity.py` with `require_verified_client_id()`. It reads FastMCP's verified access-token context via `get_access_token()` and accepts only a non-empty server-configured `client_id`. This is a token-client identifier, not necessarily a platform principal; authorization requires the explicit mapping described below. Missing context, exceptions, blank IDs, or tokens without `client_id` fail closed. Unit tests in `tests/unit/test_auth_identity.py` cover successful resolution and fail-closed cases.

This helper is intentionally not wired into MCP middleware, tools, or account routing yet. Existing configured `client_id` values (including the legacy `owner`) are only token-client identifiers today; they become platform principals only after an explicit provisioning/mapping policy is agreed and tested. In particular, the legacy fallback is not proof of end-user identity.

FastMCP's documented API exposes the current verified token through `get_access_token()`, including `client_id`, `scopes`, and `claims`; identity resolution uses the server-configured client ID rather than arbitrary claims. Reference: <https://gofastmcp.com/servers/authorization>.

Remaining before integration: latest commit CI; test the exact installed/locked FastMCP version with the actual `StaticTokenVerifier` setup; decide whether token client IDs map directly to principal IDs or require a separate provisioned mapping; and complete backup/restore and branch-divergence review. Phase 1 remains open.



### Implementation checkpoint — explicit principal mapping seam

The feature branch now includes `principal_mapping.py`, a pure resolver for a
server-owned JSON mapping from verified `client_id` values to stable
`principal_id` values. It rejects missing/invalid configuration, blank IDs,
duplicate JSON client keys, and unknown clients. Multiple distinct client IDs may
map to the same stable principal, allowing one user to use multiple client apps. It does not auto-create
principals and does not fall back from an unknown client ID to itself.

This is deliberately not wired into FastMCP request handling or the account
registry yet. Before wiring it, the service must define how mapping configuration
is supplied, provision matching registry principals, preserve audit `client_id`
separately from authorization `principal_id`, and choose an explicit legacy
single-token mapping. These tests validate only the mapping policy seam, not
end-to-end token verification or account isolation.


## 14. Explicit mapping enforced by the account-authorization seam (2026-10-24)

Updated `account_authorization.py` so its helper no longer treats a verified
token's `client_id` as a platform principal by default. The helper now requires
a server-supplied `client_to_principal` mapping, resolves the verified client ID
through that mapping, and only then asks the registry to authorize the requested
account/action. Unmapped clients fail closed; a mapped but unprovisioned principal
is denied by the registry. The mapping is a function argument for testability and
must come from trusted server configuration, never from tool arguments.

Regression tests cover mapped access, cross-account denial, absent identity,
unmapped-client denial, and a mapping that points to an unprovisioned principal.
This is still an internal helper only: no FastMCP middleware or existing WhatsApp
tool invokes it yet, so it must not be represented as end-to-end isolation.


## 15. Registry transaction and revocation boundaries (2026-10-26)

The isolated registry now uses SQLite write transactions (BEGIN IMMEDIATE) around account creation, sharing grants, sharing revocation, and account lifecycle status changes. This serializes authorization decisions and writes across separate registry connections, not just threads sharing one AccountRegistry instance. Grant upserts must never downgrade an existing owner row; a conflicting grant against an owner is denied and rolled back. Offline tests cover this invariant and concurrent grant/revoke calls from two registry instances.

These transactions protect registry consistency only. They do not make an external WhatsApp send atomic with authorization: a permission can be revoked after a tool checks access but before a bridge operation finishes. Before account authorization is wired into tools, the service must define an in-flight revocation contract. At minimum, authorize immediately before dispatch, bind the selected account to trusted request context (never a user-supplied account ID alone), and have the session manager reject new work after revocation. For stronger guarantees, use account-generation/fencing tokens checked by the bridge at dispatch and on queued jobs; cancel pending jobs and make any already-dispatched send outcome explicit as potentially ambiguous. Do not claim revocation can retract a message already handed to WhatsApp.

Lifecycle status is also a policy seam, not proof of WhatsApp authentication. Setting a registry row to active must eventually be driven by a trusted lifecycle controller after the bridge confirms a valid linked-device session; an owner or tool argument must not be allowed to manufacture a successful pairing state. The current registry is not wired to such a controller, so these changes do not yet establish production enforcement.

**Verification boundary:** hosted CI validates unit behavior only. No multi-process deployment, Docker backup/restore rehearsal, live pairing, or real-session recovery was performed in this environment. Phase 1 and runtime multi-user implementation remain blocked on the operational rehearsal and repository history review.


## 16. Principal disablement and ownership recovery

A principal marked `disabled` is denied by the registry while disabled, but the principal row and its existing account grants are retained. Re-enabling that principal makes still-active grants effective again. This is reversible suspension, not permanent revocation; operators must use `revoke_access` to remove a non-owner grant.

The current isolated registry has no ownership-transfer or audited break-glass recovery operation. Disabling an account's sole owner can therefore leave its account without an active manager until that owner is re-enabled. Do not wire principal disablement into a user-deletion workflow or permit irreversible account-owner deactivation until an audited ownership-transfer/recovery design exists. That design must prevent self-approval, preserve account/session ownership history, and make the transfer atomic.

This is a known pre-integration limitation, not a claim that production account recovery is implemented.


## 17. MCP resources and signed-media tenant boundary

The chat, message, and media MCP resource handlers now explicitly require `whatsapp:read` (or `whatsapp:full`) and return generic errors instead of exposing downstream exception text. Offline tests cover missing auth context, send-only tokens, read/full tokens, and redaction of bridge/local file errors.

This closes only the transport-scope gap. The current signed media URL payload carries message ID, chat JID, filename, and expiry; the signing key is derived from the legacy bearer token or the first token in the multi-token configuration. The short-link database and media directory are shared. There is no account ID or principal authorization in the signed claims/short-link records today. These links are bearer capabilities until expiry, so anyone who receives one can use it.

Before multi-user production use, media URL issuance must verify the caller's trusted principal and account grant; claims and short-link records must bind to an internal account ID; media caches must be partitioned by account; and signing-key rotation/revocation semantics must be explicit. A valid HMAC alone is not proof that the current requester owns the referenced account. Do not claim tenant isolation for media or resources until this design is implemented and cross-account negative tests pass.
