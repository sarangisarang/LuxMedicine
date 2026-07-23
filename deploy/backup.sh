#!/usr/bin/env bash
# Nightly backup of everything a container recreate would keep but a volume loss would not.
#
# What is backed up, and why each matters:
#   - the Postgres database: the corpus, the users, and the hash-chained audit trail. This is
#     the asset. A `pg_dump` is portable across Postgres versions and restores into an empty
#     database, unlike a raw volume copy.
#   - the nginx access log: who reached the app and from where. It lives only in Docker's
#     memory and vanishes on the next reverse-proxy restart. Its *content* (which identity
#     asked what) is already permanent in audit_log; this keeps the network view — IPs, times —
#     that the audit deliberately does not record.
#
# What is NOT backed up here, on purpose:
#   - the PDF store. It is content-addressed and every file is re-downloadable from its public
#     source (gesetze-im-internet.de, the CDC), so backing up 30 MB of recoverable bytes nightly
#     earns nothing. The database row carries the sha256, so a lost store is refetched and
#     verified, not reconstructed from a guess.
#
# Retention: 14 daily copies, then pruned. The dump is ~73 MB (mostly the 14 k embedding
# vectors, which are the bulk of the corpus), so 14 copies is ~1 GB against 40 GB free —
# comfortable, and enough to survive a mistake noticed within two weeks. Adjust KEEP otherwise.
#
# Restore, tested (2026-07-23): pg_restore into an empty database rebuilds 14 126 chunks / 27
# documents / 147 audit rows, and verify_chain confirms both clinics' hash chains intact on the
# restored copy — so this is a backup that has been shown to come back, not just to be written.
#   docker exec luxmedicine-prod-db-1 psql -U luxmed -d postgres -c 'CREATE DATABASE restore_test;'
#   cat db-<stamp>.dump | docker exec -i luxmedicine-prod-db-1 pg_restore -U luxmed -d restore_test --no-owner
#
# This does not touch ATOB. It reads from the luxmedicine-prod containers only.

set -euo pipefail

DIR="/opt/luxmedicine/backups"
KEEP=14
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$DIR"

# --- database: a real dump, not a volume snapshot ---------------------------------------
# --clean --if-exists so the file restores over an existing database; custom format (-Fc) so a
# single table can be pulled out later without replaying everything.
db_file="$DIR/db-${STAMP}.dump"
docker exec luxmedicine-prod-db-1 pg_dump -U luxmed -Fc --clean --if-exists luxmedicine > "$db_file"

# Refuse to keep a dump that is suspiciously small — a truncated backup that looks like a backup
# is worse than none, because it is trusted. A real dump of this corpus is well over 1 MB.
size=$(stat -c%s "$db_file")
if [ "$size" -lt 1000000 ]; then
  echo "ABORT: db dump is only ${size} bytes — refusing to keep a likely-truncated backup" >&2
  rm -f "$db_file"
  exit 1
fi

# --- nginx access log: the network view the audit trail omits ---------------------------
# Best-effort: if the container is not up, the DB dump still succeeded and that is the asset.
log_file="$DIR/nginx-access-${STAMP}.log.gz"
if docker ps --format '{{.Names}}' | grep -q '^deploy-nginx-1$'; then
  docker logs deploy-nginx-1 2>&1 | grep 'luxmedicin' | gzip > "$log_file" || true
fi

# --- prune ------------------------------------------------------------------------------
ls -1t "$DIR"/db-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -1t "$DIR"/nginx-access-*.log.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "backup ok: $(basename "$db_file") ($(numfmt --to=iec "$size")), $(ls -1 "$DIR"/db-*.dump | wc -l) kept"
