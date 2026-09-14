#!/usr/bin/env bash
# Ночной бэкап установки: база, файлы и копия на второй сервер.
#
# Ставится по cron (см. deploy/backup.cron). Запускать можно и руками —
# скрипт идемпотентен и ничего не удаляет, кроме собственных старых копий.
#
#   bash deploy/backup.sh              # обычный прогон
#   bash deploy/backup.sh --media      # принудительно и файлы тоже
#
# Почему так устроено:
# - база маленькая (десятки МБ) и меняется каждый день — дампим ежедневно;
# - файлы большие (сотни МБ) и почти не меняются — раз в неделю, иначе
#   диск съедят копии одних и тех же фотографий;
# - копия уезжает на ВТОРОЙ сервер: бэкап, лежащий рядом с базой, не
#   спасает от потери самого сервера.
set -euo pipefail

DIR="${BACKUP_DIR:-/opt/backups}"
PROJECT_DIR="${PROJECT_DIR:-/opt/padacha-app}"
COMPOSE="docker compose -f $PROJECT_DIR/docker-compose.prod.yml"
KEEP_DB="${KEEP_DB:-14}"        # сколько ежедневных дампов базы хранить
KEEP_MEDIA="${KEEP_MEDIA:-4}"   # сколько недельных архивов файлов
MEDIA_VOLUME="${MEDIA_VOLUME:-hub_media}"
#: Куда отправлять копию. Пусто — только локально.
OFFSITE="${OFFSITE:-}"          # напр. root@сервер:/opt/backups-padacha
OFFSITE_PORT="${OFFSITE_PORT:-22}"

TS=$(date +%Y%m%d-%H%M)
mkdir -p "$DIR"
cd "$PROJECT_DIR"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── база ─────────────────────────────────────────────────────────────────
DB_FILE="$DIR/db-$TS.sql.gz"
log "дамп базы → $(basename "$DB_FILE")"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$DB_FILE"

# Пустой дамп — это провал, а не бэкап: лучше узнать сейчас, чем при
# восстановлении. 1 КБ — заведомо меньше любого осмысленного дампа.
if [ "$(stat -c %s "$DB_FILE")" -lt 1024 ]; then
    rm -f "$DB_FILE"
    log "ОШИБКА: дамп пустой, бэкап не сохранён"
    exit 1
fi
log "база: $(du -h "$DB_FILE" | cut -f1)"

# ── файлы (раз в неделю или по флагу) ────────────────────────────────────
WANT_MEDIA=false
[ "${1:-}" = "--media" ] && WANT_MEDIA=true
[ "$(date +%u)" = "7" ] && WANT_MEDIA=true   # воскресенье
if $WANT_MEDIA; then
    MEDIA_FILE="$DIR/media-$TS.tar.gz"
    log "архив файлов → $(basename "$MEDIA_FILE")"
    docker run --rm -v "$MEDIA_VOLUME":/m alpine tar czf - -C /m . > "$MEDIA_FILE"
    log "файлы: $(du -h "$MEDIA_FILE" | cut -f1)"
fi

# ── чистка старых ────────────────────────────────────────────────────────
# ls -t: свежие сверху; хвост списка удаляем.
ls -t "$DIR"/db-*.sql.gz 2>/dev/null | tail -n +$((KEEP_DB + 1)) | xargs -r rm -f
ls -t "$DIR"/media-*.tar.gz 2>/dev/null | tail -n +$((KEEP_MEDIA + 1)) | xargs -r rm -f

# ── копия на второй сервер ───────────────────────────────────────────────
if [ -n "$OFFSITE" ]; then
    log "копия на $OFFSITE"
    if scp -P "$OFFSITE_PORT" -o ConnectTimeout=30 -o StrictHostKeyChecking=accept-new \
            "$DB_FILE" ${MEDIA_FILE:+"$MEDIA_FILE"} "$OFFSITE/" 2>/dev/null; then
        # На той стороне тоже чистим, иначе однажды кончится диск.
        ssh -p "$OFFSITE_PORT" -o ConnectTimeout=30 "${OFFSITE%%:*}" \
            "ls -t ${OFFSITE#*:}/db-*.sql.gz 2>/dev/null | tail -n +$((KEEP_DB + 1)) | xargs -r rm -f; \
             ls -t ${OFFSITE#*:}/media-*.tar.gz 2>/dev/null | tail -n +$((KEEP_MEDIA + 1)) | xargs -r rm -f" || true
        log "копия отправлена"
    else
        # Не падаем: локальный бэкап уже сделан, а сеть могла моргнуть.
        log "ВНИМАНИЕ: копию отправить не удалось"
    fi
fi

log "готово. Всего копий: $(ls "$DIR" | wc -l), занято $(du -sh "$DIR" | cut -f1)"
