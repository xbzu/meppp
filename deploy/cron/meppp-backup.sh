#!/bin/sh
set -eu
umask 077

app_dir="${MEPPP_APP_DIR:-/opt/meppp}"
host_backup_dir="${MEPPP_HOST_BACKUP_DIR:-/srv/meppp/data/backups/sqlite}"
offsite_dir="${MEPPP_OFFSITE_DIR:-/www/backup/meppp.com}"
lock_dir="${MEPPP_BACKUP_LOCK_DIR:-/run/lock/meppp-backup.lock.d}"

command -v docker >/dev/null
command -v sha256sum >/dev/null
test -d "$app_dir"
test -d "$offsite_dir"

if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "another MEPPP backup task is already running" >&2
    exit 1
fi
cleanup_lock() {
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup_lock EXIT
trap 'exit 1' HUP INT TERM

cd "$app_dir"
docker compose exec -T app python manage.py backup_sqlite

test -d "$host_backup_dir"
source_device=$(stat -c '%d' "$host_backup_dir")
offsite_device=$(stat -c '%d' "$offsite_dir")
if [ "$source_device" = "$offsite_device" ]; then
    echo "refusing backup copy: MEPPP_OFFSITE_DIR is on the same filesystem" >&2
    exit 1
fi

latest=$(docker compose exec -T app sh -c \
    'find /data/backups/sqlite -maxdepth 1 -name "meppp-*.sqlite3" -type f | sort | tail -1')
test -n "$latest"
docker compose exec -T app python manage.py restore_sqlite "$latest"

if command -v rsync >/dev/null; then
    rsync -a --ignore-existing \
        --include='meppp-*.sqlite3' \
        --include='meppp-*.sqlite3.sha256' \
        --exclude='*' \
        "$host_backup_dir/" "$offsite_dir/"
else
    for source in "$host_backup_dir"/meppp-*.sqlite3 \
        "$host_backup_dir"/meppp-*.sqlite3.sha256; do
        test -f "$source"
        destination="$offsite_dir/$(basename "$source")"
        if [ ! -e "$destination" ]; then
            cp -p "$source" "$destination"
        fi
    done
fi

(
    cd "$offsite_dir"
    found=0
    for manifest in meppp-*.sqlite3.sha256; do
        if [ ! -f "$manifest" ]; then
            continue
        fi
        found=1
        sha256sum --check "$manifest"
    done
    test "$found" -eq 1
)

echo "MEPPP backup, restore drill, independent copy, and checksum verification passed"
