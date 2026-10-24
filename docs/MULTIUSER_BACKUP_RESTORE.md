# Safe Backup and Restore Rehearsal — WhatsApp MCP

Status: procedure documented; **not executed** against the deployment. Never use the production volumes as restore targets.

## Scope

The current Compose configuration declares three named volumes:

- `wa-store`: bridge-owned WhatsApp device/session database and message database; bridge writes, MCP mounts read-only.
- `wa-audit`: MCP audit log and other configured SQLite databases (scheduling, transcription, short URLs).
- `wa-media`: decrypted media cache used by signed media URLs.

Compose prefixes volume names with the project name in normal deployments. Discover the actual names; do not assume the names are literally `wa-store`, etc. Also inspect the effective Compose configuration and environment for custom bind mounts or overridden paths before taking a backup.

## 1. Pre-flight and protect the source

Run from the deployment directory, with the correct Compose project selected:

```bash
docker compose config
docker compose ps
docker volume ls
docker inspect whatsapp-bridge whatsapp-mcp
```

Record the effective volume names and bind mounts. Do not print or attach `.env`, bearer tokens, QR output, session keys, or volume contents to an issue or pull request.

Set these variables to the exact existing volume names discovered above and a private backup directory with sufficient free space:

```bash
export STORE_VOL='<actual-compose-prefixed-wa-store-volume>'
export AUDIT_VOL='<actual-compose-prefixed-wa-audit-volume>'
export MEDIA_VOL='<actual-compose-prefixed-wa-media-volume>'
export BACKUP_DIR="$PWD/private-backups"
umask 077
mkdir -p "$BACKUP_DIR"
```

Verify each source volume exists before proceeding:

```bash
docker volume inspect "$STORE_VOL" "$AUDIT_VOL" "$MEDIA_VOL"
```

## 2. Make a consistent cold copy

The safest straightforward rehearsal is a planned maintenance window: stop the MCP writer/workers and then the bridge so SQLite and session files are quiescent. This causes downtime; coordinate it with the operator.

```bash
docker compose stop mcp
docker compose stop bridge
```

Confirm both are stopped. Copy each volume to a separate archive. These commands mount the source read-only and write only to the host backup directory:

```bash
docker run --rm -v "$STORE_VOL:/source:ro" -v "$BACKUP_DIR:/backup" alpine:3.20 sh -c 'cd /source && tar -czf /backup/wa-store.tgz .'
docker run --rm -v "$AUDIT_VOL:/source:ro" -v "$BACKUP_DIR:/backup" alpine:3.20 sh -c 'cd /source && tar -czf /backup/wa-audit.tgz .'
docker run --rm -v "$MEDIA_VOL:/source:ro" -v "$BACKUP_DIR:/backup" alpine:3.20 sh -c 'cd /source && tar -czf /backup/wa-media.tgz .'
sha256sum "$BACKUP_DIR"/wa-store.tgz "$BACKUP_DIR"/wa-audit.tgz "$BACKUP_DIR"/wa-media.tgz > "$BACKUP_DIR/SHA256SUMS"
```

Check the archive listing and hashes without exposing contents:

```bash
tar -tzf "$BACKUP_DIR/wa-store.tgz" >/dev/null
tar -tzf "$BACKUP_DIR/wa-audit.tgz" >/dev/null
tar -tzf "$BACKUP_DIR/wa-media.tgz" >/dev/null
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
```

Keep the backup directory private and encrypted at rest according to the deployment's secret/data policy. A backup contains WhatsApp session credentials and private messages; possession of it may permit account access. Do not commit it or upload it to GitHub.

Restart the existing services after the archive is verified:

```bash
docker compose start bridge
docker compose start mcp
docker compose ps
```

Confirm bridge health and MCP health using the deployment's normal operational checks. Do not delete the original volumes.

## 3. Restore rehearsal into disposable volumes only

Create three new volume names reserved for the rehearsal. Do not substitute the production volume variables:

```bash
export TEST_STORE_VOL="wa-restore-test-store-$(date +%Y%m%d%H%M%S)"
export TEST_AUDIT_VOL="wa-restore-test-audit-$(date +%Y%m%d%H%M%S)"
export TEST_MEDIA_VOL="wa-restore-test-media-$(date +%Y%m%d%H%M%S)"
docker volume create "$TEST_STORE_VOL"
docker volume create "$TEST_AUDIT_VOL"
docker volume create "$TEST_MEDIA_VOL"
```

Verify the names are distinct from all production volumes before extracting. Extract each archive only into its corresponding new disposable volume:

```bash
docker run --rm -v "$TEST_STORE_VOL:/target" -v "$BACKUP_DIR:/backup:ro" alpine:3.20 sh -c 'cd /target && tar -xzf /backup/wa-store.tgz'
docker run --rm -v "$TEST_AUDIT_VOL:/target" -v "$BACKUP_DIR:/backup:ro" alpine:3.20 sh -c 'cd /target && tar -xzf /backup/wa-audit.tgz'
docker run --rm -v "$TEST_MEDIA_VOL:/target" -v "$BACKUP_DIR:/backup:ro" alpine:3.20 sh -c 'cd /target && tar -xzf /backup/wa-media.tgz'
```

Run SQLite integrity checks against every restored `*.db` file in the test copies. This uses Python's SQLite library, avoiding assumptions that the image has the `sqlite3` CLI:

```bash
for vol in "$TEST_STORE_VOL" "$TEST_AUDIT_VOL"; do
  docker run --rm -v "$vol:/data:ro" python:3.12-slim python -c '
import pathlib, sqlite3, sys
files = sorted(pathlib.Path("/data").rglob("*.db"))
if not files:
    print("No .db files found under", sys.argv[1] if len(sys.argv) > 1 else "/data")
for path in files:
    uri = "file:" + str(path) + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    result = con.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"{path}: {result}")
    if result != "ok":
        raise SystemExit(1)
    con.close()
' 
done
```

The command checks the test volume's databases. Manually verify that expected database files exist in the restored tree, that application-specific tables are readable, and that the restored media files and audit records are present. If any expected DB is nested or uses a non-`.db` extension, include it explicitly in the check. `PRAGMA integrity_check` is necessary but does not prove application-level consistency or that a WhatsApp session can reconnect.

For a true application-level rehearsal, start a **separate test Compose project** configured to mount only the three test volumes, use a non-production MCP token and an isolated network/ports, and do not expose its bridge publicly. Validate read-only DB access and service startup without pairing or sending messages. Do not point the production Compose project at the test volumes.

## 4. Consistency and recovery limitations

- Copying a live SQLite main database file without coordinating its WAL/SHM files can produce an incomplete or inconsistent backup. Stop all writers for a cold copy, or use a SQLite-supported online backup mechanism and verify the result.
- WhatsApp device/session state and messages are in different databases in the store volume. Preserve the whole volume and related state together; do not restore only `messages.db`.
- Audit/scheduling/transcription/short-link databases and media are separate volumes and should be snapshotted at the same maintenance point.
- Restoring a snapshot does not prove the external WhatsApp session remains valid. Reconnect tests should be conducted only on an explicitly authorized test copy/account and never by concurrently running two clients against the same copied session.
- Do not delete test volumes until the rehearsal evidence has been recorded; do not delete or overwrite production volumes as part of cleanup.

## Evidence record

Record the timestamp, Compose project name, volume names (or redacted identifiers), archive sizes, SHA-256 results, restored database filenames, each integrity-check result, and application-level read/startup checks. Keep secrets, message content, QR payloads, session values, and personal data out of the report.

Current status: **not run**. The repository-editing environment has no Docker daemon or access to the deployment host/volumes. Phase 1 cannot close until an operator runs this procedure on a disposable restore target and records the evidence.
