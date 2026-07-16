#!/bin/sh
set -eu
umask 077

app_dir="${MEPPP_APP_DIR:-/opt/meppp}"
host_data_dir="${MEPPP_HOST_DATA_DIR:-/srv/meppp/data}"
host_backup_dir="${host_data_dir}/backups/sqlite"
host_media_dir="${host_data_dir}/media"
lock_dir="${MEPPP_BACKUP_PREPARE_LOCK_DIR:-/run/lock/meppp-backup-prepare.lock.d}"

command -v docker >/dev/null
command -v sha256sum >/dev/null
test -d "$app_dir"
test -d "$host_media_dir"

if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "another MEPPP backup preparation is already running" >&2
    exit 1
fi
cleanup_lock() {
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup_lock EXIT
trap 'exit 1' HUP INT TERM

cd "$app_dir"
docker compose exec -T app python manage.py backup_sqlite

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
if [ -n "$(find "$host_media_dir" -type f ! -name '*.webp' -print -quit)" ]; then
    echo "refusing media backup: unexpected non-WebP file found" >&2
    exit 1
fi

snapshot_id=$(basename "$latest_name" .sqlite3)
media_manifest="${snapshot_id}-media.sha256"
media_manifest_tmp="${host_backup_dir}/.${media_manifest}.tmp"
(
    cd "$host_data_dir"
    find media -type f -name '*.webp' -print0 | sort -z | \
        xargs -0 -r sha256sum
) > "$media_manifest_tmp"
chmod 600 "$media_manifest_tmp"
mv "$media_manifest_tmp" "${host_backup_dir}/${media_manifest}"

(
    cd "$host_backup_dir"
    sha256sum --check "${latest_name}.sha256"
)
if [ -s "${host_backup_dir}/${media_manifest}" ]; then
    (
        cd "$host_data_dir"
        sha256sum --check "backups/sqlite/${media_manifest}"
    )
else
    test -z "$(find "$host_media_dir" -type f -name '*.webp' -print -quit)"
fi

echo "MEPPP source backup, restore drill, media verification, and manifests passed: ${latest_name}"
