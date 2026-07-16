#!/bin/sh
set -eu
umask 077

offsite_volume="${MEPPP_OFFSITE_VOLUME:?Set MEPPP_OFFSITE_VOLUME}"
expected_volume_uuid="${MEPPP_OFFSITE_VOLUME_UUID:?Set MEPPP_OFFSITE_VOLUME_UUID}"
offsite_dir="${MEPPP_OFFSITE_DIR:?Set MEPPP_OFFSITE_DIR}"
remote="${MEPPP_BACKUP_REMOTE:?Set MEPPP_BACKUP_REMOTE}"
ssh_key="${MEPPP_BACKUP_SSH_KEY:?Set MEPPP_BACKUP_SSH_KEY}"
known_hosts="${MEPPP_BACKUP_KNOWN_HOSTS:?Set MEPPP_BACKUP_KNOWN_HOSTS}"
lock_file="${MEPPP_BACKUP_LOCK_FILE:-${offsite_dir}/.pull.lock}"
max_age_seconds="${MEPPP_BACKUP_MAX_AGE_SECONDS:-93600}"
minimum_free_kib="${MEPPP_BACKUP_MINIMUM_FREE_KIB:-1048576}"

for command_path in /usr/bin/rsync /usr/bin/shasum /usr/bin/sqlite3 \
    /usr/bin/shlock /usr/bin/plutil /usr/sbin/diskutil; do
    test -x "$command_path"
done
test -d "$offsite_volume"
test -d "$offsite_dir"
test -f "$ssh_key"
test -f "$known_hosts"
case "$lock_file" in
    /*) ;;
    *)
        echo "refusing backup: lock file path must be absolute" >&2
        exit 1
        ;;
esac
lock_parent=$(dirname "$lock_file")
test -d "$lock_parent"
if [ -L "$lock_parent" ] || [ -L "$lock_file" ]; then
    echo "refusing backup: lock path must not be a symbolic link" >&2
    exit 1
fi

volume_uuid() {
    /usr/sbin/diskutil info -plist "$offsite_volume" | \
        /usr/bin/plutil -extract VolumeUUID raw -o - -
}

actual_volume_uuid=$(volume_uuid)
if [ "$actual_volume_uuid" != "$expected_volume_uuid" ]; then
    echo "refusing backup: external volume UUID mismatch" >&2
    exit 1
fi
root_device=$(stat -f '%d' /)
offsite_device=$(stat -f '%d' "$offsite_volume")
if [ "$root_device" = "$offsite_device" ]; then
    echo "refusing backup: destination is on the Mac internal filesystem" >&2
    exit 1
fi

canonical_volume=$(cd "$offsite_volume" && pwd -P)
assert_offsite_destination() {
    canonical_destination=$(cd "$offsite_dir" && pwd -P)
    case "${canonical_destination}/" in
        "${canonical_volume}/"*) ;;
        *)
            echo "refusing backup: destination is outside the expected external volume" >&2
            exit 1
            ;;
    esac
    destination_device=$(stat -f '%d' "$offsite_dir")
    if [ "$destination_device" != "$offsite_device" ] || \
        [ "$destination_device" = "$root_device" ]; then
        echo "refusing backup: destination filesystem does not match the external volume" >&2
        exit 1
    fi
}
assert_offsite_destination

available_kib=$(df -Pk "$offsite_volume" | awk 'NR == 2 {print $4}')
case "$available_kib" in
    ''|*[!0-9]*)
        echo "could not determine external backup free space" >&2
        exit 1
        ;;
esac
if [ "$available_kib" -lt "$minimum_free_kib" ]; then
    echo "refusing backup: external volume free-space floor not met" >&2
    exit 1
fi

if [ -L "$offsite_dir/media" ]; then
    echo "refusing backup: media destination is a symbolic link" >&2
    exit 1
fi
mkdir -p "$offsite_dir/media"
if [ "$(stat -f '%d' "$offsite_dir/media")" != "$offsite_device" ]; then
    echo "refusing backup: media destination is outside the external volume" >&2
    exit 1
fi
if ! /usr/bin/shlock -f "$lock_file" -p "$$"; then
    echo "another MEPPP offsite pull is already running" >&2
    exit 1
fi
cleanup_lock() {
    rm -f "$lock_file"
}
trap cleanup_lock EXIT
trap 'exit 1' HUP INT TERM

RSYNC_RSH="/usr/bin/ssh -i ${ssh_key} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${known_hosts}"
export RSYNC_RSH

/usr/bin/rsync -rtp --ignore-existing \
    --include='meppp-*.sqlite3' \
    --include='meppp-*.sqlite3.sha256' \
    --include='meppp-*-media.sha256' \
    --exclude='*' \
    "${remote}:/backups/sqlite/" "$offsite_dir/"
/usr/bin/rsync -rtp --ignore-existing \
    --include='*/' --include='*.webp' --include='*.mp4' --include='*.webm' --exclude='*' \
    "${remote}:/media/" "$offsite_dir/media/"

if [ "$(volume_uuid)" != "$expected_volume_uuid" ]; then
    echo "refusing backup result: external volume changed during transfer" >&2
    exit 1
fi
assert_offsite_destination
if [ "$(stat -f '%d' "$offsite_dir/media")" != "$offsite_device" ]; then
    echo "refusing backup result: media destination filesystem changed" >&2
    exit 1
fi
if [ -n "$(find "$offsite_dir/media" -type l -print -quit)" ]; then
    echo "refusing backup result: symbolic link found" >&2
    exit 1
fi
if [ -n "$(find "$offsite_dir/media" -type f ! \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)" ]; then
    echo "refusing backup result: unexpected media file found" >&2
    exit 1
fi

latest=$(find "$offsite_dir" -maxdepth 1 -name 'meppp-*.sqlite3' -type f | sort | tail -1)
test -n "$latest"
latest_name=$(basename "$latest")
snapshot_id=$(basename "$latest_name" .sqlite3)
database_manifest="${latest_name}.sha256"
media_manifest="${snapshot_id}-media.sha256"
test -f "${offsite_dir}/${database_manifest}"
test -f "${offsite_dir}/${media_manifest}"

(
    cd "$offsite_dir"
    /usr/bin/shasum -a 256 --check "$database_manifest"
)
integrity=$(/usr/bin/sqlite3 "file:${latest}?mode=ro&immutable=1" 'PRAGMA quick_check;')
if [ "$integrity" != "ok" ]; then
    echo "offsite SQLite integrity check failed" >&2
    exit 1
fi
if [ -s "${offsite_dir}/${media_manifest}" ]; then
    (
        cd "$offsite_dir"
        /usr/bin/shasum -a 256 --check "$media_manifest"
    )
else
    test -z "$(find "$offsite_dir/media" -type f \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)"
fi

now_epoch=$(date +%s)
snapshot_epoch=$(stat -f '%m' "$latest")
snapshot_age=$((now_epoch - snapshot_epoch))
if [ "$snapshot_age" -lt 0 ] || [ "$snapshot_age" -gt "$max_age_seconds" ]; then
    echo "offsite backup is outside the allowed freshness window" >&2
    exit 1
fi

success_tmp="${offsite_dir}/.LAST_SUCCESS.$$"
{
    printf 'completed_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'snapshot=%s\n' "$latest_name"
    printf 'snapshot_age_seconds=%s\n' "$snapshot_age"
    printf 'volume_uuid=%s\n' "$actual_volume_uuid"
    printf 'database_integrity=ok\n'
    printf 'media_manifest=%s\n' "$media_manifest"
} > "$success_tmp"
chmod 600 "$success_tmp"
mv "$success_tmp" "${offsite_dir}/LAST_SUCCESS"

echo "MEPPP offsite pull and independent checksum verification passed: ${latest_name}"
