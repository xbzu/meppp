#!/bin/sh
set -eu
umask 077

app_dir="${MEPPP_APP_DIR:-/opt/meppp}"
host_data_dir="${MEPPP_HOST_DATA_DIR:-/srv/meppp/data}"
host_backup_dir="${host_data_dir}/backups/sqlite"
host_media_dir="${host_data_dir}/media"
offsite_dir="${MEPPP_OFFSITE_DIR:-/www/backup/meppp.com}"
lock_dir="${MEPPP_BACKUP_LOCK_DIR:-/run/lock/meppp-backup.lock.d}"

command -v docker >/dev/null
command -v sha256sum >/dev/null
test -d "$app_dir"
test -d "$offsite_dir"
test -d "$host_media_dir"

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
backup_device=$(stat -c '%d' "$host_backup_dir")
media_device=$(stat -c '%d' "$host_media_dir")
offsite_device=$(stat -c '%d' "$offsite_dir")
if [ "$backup_device" = "$offsite_device" ] || [ "$media_device" = "$offsite_device" ]; then
    echo "refusing backup copy: database or media source shares the offsite filesystem" >&2
    exit 1
fi

latest=$(docker compose exec -T app sh -c \
    'find /data/backups/sqlite -maxdepth 1 -name "meppp-*.sqlite3" -type f | sort | tail -1')
test -n "$latest"
latest_name=$(basename "$latest")
test -f "${host_backup_dir}/${latest_name}"
test -f "${host_backup_dir}/${latest_name}.sha256"
docker compose exec -T app python manage.py restore_sqlite "$latest"
docker compose exec -T app python manage.py verify_media \
    --database "$latest" --media-root /data/media

if [ -n "$(find "$host_media_dir" -type l -print -quit)" ]; then
    echo "refusing media backup: symbolic link found" >&2
    exit 1
fi

offsite_media_dir="${offsite_dir}/media"
mkdir -p "$offsite_media_dir"

if command -v rsync >/dev/null; then
    rsync -a --ignore-existing \
        --include='meppp-*.sqlite3' \
        --include='meppp-*.sqlite3.sha256' \
        --exclude='*' \
        "$host_backup_dir/" "$offsite_dir/"
    rsync -a --ignore-existing \
        --include='*/' --include='*.webp' --exclude='*' \
        "$host_media_dir/" "$offsite_media_dir/"
else
    for source in "$host_backup_dir"/meppp-*.sqlite3 \
        "$host_backup_dir"/meppp-*.sqlite3.sha256; do
        test -f "$source"
        destination="$offsite_dir/$(basename "$source")"
        if [ ! -e "$destination" ]; then
            cp -p "$source" "$destination"
        fi
    done
    find "$host_media_dir" -type f -name '*.webp' | while IFS= read -r source; do
        relative=${source#"$host_media_dir"/}
        destination="${offsite_media_dir}/${relative}"
        mkdir -p "$(dirname "$destination")"
        if [ ! -e "$destination" ]; then
            cp -p "$source" "$destination"
        fi
    done
fi

snapshot_id=$(basename "$latest_name" .sqlite3)
media_manifest="${snapshot_id}-media.sha256"
media_manifest_tmp="${host_backup_dir}/.${media_manifest}.tmp"
(
    cd "$host_media_dir"
    find . -type f -name '*.webp' -print0 | sort -z | \
        xargs -0 -r sha256sum | sed 's#  \./#  media/#'
) > "$media_manifest_tmp"
chmod 600 "$media_manifest_tmp"
mv "$media_manifest_tmp" "${host_backup_dir}/${media_manifest}"
cp -p "${host_backup_dir}/${media_manifest}" "${offsite_dir}/${media_manifest}"

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

if [ -s "${offsite_dir}/${media_manifest}" ]; then
    (
        cd "$offsite_dir"
        sha256sum --check "$media_manifest"
    )
else
    test -z "$(find "$offsite_media_dir" -type f -name '*.webp' -print -quit)"
fi

echo "MEPPP database and media backup, restore drill, independent copy, and checksum verification passed"
