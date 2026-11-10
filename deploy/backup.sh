#!/usr/bin/env bash
# Atomic database backups; daily cron and hub requests share the same lock.
set -euo pipefail
umask 077
DIR="${BACKUP_DIR:-/opt/backups}"
PROJECT_DIR="${PROJECT_DIR:-/opt/padacha-app}"
KEEP_DB="${KEEP_DB:-30}"
KEEP_MEDIA="${KEEP_MEDIA:-4}"
MEDIA_VOLUME="${MEDIA_VOLUME:-hub_media}"
OFFSITE="${OFFSITE:-}"
OFFSITE_PORT="${OFFSITE_PORT:-22}"
[[ "$KEEP_DB" =~ ^[1-9][0-9]*$ && "$KEEP_MEDIA" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid retention'; exit 1; }
[[ "${1:-}" = '' || "${1:-}" = '--media' ]] || { echo 'Usage: backup.sh [--media]'; exit 1; }
mkdir -p "$DIR"
chmod 700 "$DIR"
exec 9>"$DIR/.backup.lock"
flock -w 1800 9
cd "$PROJECT_DIR"
COMPOSE=(docker compose -f "$PROJECT_DIR/docker-compose.prod.yml")
TS=$(date +%Y%m%d-%H%M%S)
TMP_FILES=()
trap 'for file in "${TMP_FILES[@]}"; do rm -f -- "$file"; done' EXIT
log() { echo "[$(date --iso-8601=seconds)] $*"; }
publish() {
    local temp="$1" final="$2"
    gzip -t "$temp"
    chmod 600 "$temp"
    mv -- "$temp" "$final"
    (cd "$DIR" && sha256sum "$(basename "$final")") > "$final.sha256.tmp"
    mv -- "$final.sha256.tmp" "$final.sha256"
}
prune() {
    local prefix="$1" suffix="$2" keep="$3"
    local files=("$DIR"/"$prefix"-*"$suffix")
    local count=$((${#files[@]} - keep))
    for ((i=0; i<count; i++)); do
        rm -f -- "${files[i]}" "${files[i]}.sha256"
    done
}
DB_FILE="$DIR/db-$TS.sql.gz"
test ! -e "$DB_FILE"
TMP=$(mktemp "$DIR/.db-XXXXXX")
TMP_FILES+=("$TMP")
log 'Создание копии базы'
"${COMPOSE[@]}" exec -T db sh -c 'pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$TMP"
[ "$(stat -c %s "$TMP")" -ge 1024 ] || { log 'ОШИБКА: слишком маленький дамп'; exit 1; }
publish "$TMP" "$DB_FILE"
UPLOAD=("$DB_FILE" "$DB_FILE.sha256")
CHECKSUMS=("$(basename "$DB_FILE.sha256")")
if [[ "${1:-}" = '--media' || "$(date +%u)" = 7 ]]; then
    MEDIA_FILE="$DIR/media-$TS.tar.gz"
    TMP=$(mktemp "$DIR/.media-XXXXXX")
    TMP_FILES+=("$TMP")
    log 'Копирование файлов'
    docker run --rm -v "$MEDIA_VOLUME":/m:ro alpine tar czf - -C /m . > "$TMP"
    tar tzf "$TMP" > /dev/null
    publish "$TMP" "$MEDIA_FILE"
    UPLOAD+=("$MEDIA_FILE" "$MEDIA_FILE.sha256")
    CHECKSUMS+=("$(basename "$MEDIA_FILE.sha256")")
fi
shopt -s nullglob
prune db .sql.gz "$KEEP_DB"
prune media .tar.gz "$KEEP_MEDIA"
if [ -n "$OFFSITE" ]; then
    # Restrict destination syntax before it is used by remote shell/scp.
    [[ "$OFFSITE" =~ ^[a-zA-Z0-9_.@-]+:/[a-zA-Z0-9_./-]+$ ]] || { log 'Invalid OFFSITE'; exit 1; }
    REMOTE="${OFFSITE%%:*}"
    REMOTE_DIR="${OFFSITE#*:}"
    [[ "$REMOTE_DIR" != / && "$REMOTE_DIR" != *..* ]] || exit 1
    ssh -p "$OFFSITE_PORT" -o BatchMode=yes -o ConnectTimeout=30 "$REMOTE" "mkdir -p '$REMOTE_DIR' && chmod 700 '$REMOTE_DIR'"
    scp -P "$OFFSITE_PORT" -o BatchMode=yes -o ConnectTimeout=30 "${UPLOAD[@]}" "$OFFSITE/"
    for checksum in "${CHECKSUMS[@]}"; do
        ssh -p "$OFFSITE_PORT" -o BatchMode=yes -o ConnectTimeout=30 "$REMOTE" "cd '$REMOTE_DIR' && sha256sum -c '$checksum'"
    done
    ssh -p "$OFFSITE_PORT" -o BatchMode=yes -o ConnectTimeout=30 "$REMOTE" bash -s -- "$REMOTE_DIR" "$KEEP_DB" "$KEEP_MEDIA" <<'REMOTE_SCRIPT'
set -euo pipefail
shopt -s nullglob
cd "$1"
for kind in db media; do
    if [[ "$kind" = db ]]; then files=(db-*.sql.gz); keep="$2"; else files=(media-*.tar.gz); keep="$3"; fi
    count=$((${#files[@]} - keep))
    for ((i=0; i<count; i++)); do rm -f -- "${files[i]}" "${files[i]}.sha256"; done
done
REMOTE_SCRIPT
    log 'Копия на втором сервере проверена'
fi
log "Готово: $(basename "$DB_FILE")"
