#!/usr/bin/env bash
# Restore into a NEW database only. Never overwrite the live database.
set -euo pipefail
umask 077
if [[ $# != 2 ]]; then
    echo 'Usage: restore-db.sh /opt/backups/db-TIMESTAMP.sql.gz restore_NAME' >&2
    exit 2
fi
ARCHIVE=$(realpath "$1")
TARGET="$2"
[[ "$TARGET" =~ ^restore_[a-z0-9_]{1,50}$ ]] || { echo 'Target must start with restore_ and contain lowercase letters, digits, underscores' >&2; exit 2; }
[[ -f "$ARCHIVE" && "$ARCHIVE" = *.sql.gz ]] || { echo 'Database archive not found' >&2; exit 2; }
PROJECT_DIR="${PROJECT_DIR:-/opt/padacha-app}"
COMPOSE=(docker compose -f "$PROJECT_DIR/docker-compose.prod.yml")
gzip -t "$ARCHIVE"
if [[ -f "$ARCHIVE.sha256" ]]; then
    (cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE.sha256")")
else
    echo 'Legacy backup: no checksum available; gzip integrity checked.' >&2
fi
# createdb fails if the target exists. Never use --clean, DROP DATABASE or live DB here.
"${COMPOSE[@]}" exec -T db sh -c 'createdb -U "$POSTGRES_USER" --template=template0 "$1"' sh "$TARGET"
# On error the new database is left for inspection. The production DB is untouched.
gzip -dc "$ARCHIVE" | "${COMPOSE[@]}" exec -T db sh -c \
    'psql -X -U "$POSTGRES_USER" -d "$1" --set=ON_ERROR_STOP=1 --single-transaction' sh "$TARGET" > /dev/null
"${COMPOSE[@]}" exec -T db sh -c \
    'psql -X -U "$POSTGRES_USER" -d "$1" --set=ON_ERROR_STOP=1 -c "SELECT count(*) AS organizations FROM core_organization; SELECT count(*) AS users FROM users_user; SELECT count(*) AS orders FROM orders_order;"' sh "$TARGET"
echo "Restore verified in database: $TARGET. Production database was not changed."
