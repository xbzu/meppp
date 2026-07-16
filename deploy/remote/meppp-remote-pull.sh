#!/bin/sh
set -eu
umask 077

source_remote="${MEPPP_BACKUP_SOURCE:?Set MEPPP_BACKUP_SOURCE}"
destination="${MEPPP_BACKUP_DESTINATION:?Set MEPPP_BACKUP_DESTINATION}"
credential_dir="${CREDENTIALS_DIRECTORY:?Run with systemd credentials}"
ssh_key="${credential_dir}/source-key"
known_hosts="${credential_dir}/source-known-hosts"
max_age_seconds="${MEPPP_BACKUP_MAX_AGE_SECONDS:-93600}"
minimum_free_kib="${MEPPP_BACKUP_MINIMUM_FREE_KIB:-1048576}"
lock_dir="${MEPPP_BACKUP_LOCK_DIR:-/run/lock/meppp-remote-pull.lock.d}"

for command_name in cmp rsync sha256sum python3 ssh sync; do
    command -v "$command_name" >/dev/null
done
test -d "$destination"
python3 -c '
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_absolute():
    raise SystemExit("backup destination must be absolute")
current = Path(path.anchor)
for part in path.parts[1:]:
    current /= part
    if stat.S_ISLNK(os.lstat(current).st_mode):
        raise SystemExit(f"backup destination parent is a symbolic link: {current}")
' "$destination"
test "$(stat -c '%u' "$destination")" = "$(id -u)"
test "$(stat -c '%a' "$destination")" = 700
for reserved_path in media snapshots quarantine LAST_SUCCESS LAST_FAILURE MONITOR_FAILURE; do
    if [ -L "${destination}/${reserved_path}" ]; then
        echo "refusing backup: destination child is a symbolic link" >&2
        exit 1
    fi
done
test -f "$ssh_key"
test -f "$known_hosts"
for credential_path in "$ssh_key" "$known_hosts"; do
    case "$(stat -c '%a' "$credential_path")" in
        400|600) ;;
        *)
            echo "refusing backup: credential permissions are too broad" >&2
            exit 1
            ;;
    esac
done
case "${ssh_key}${known_hosts}" in
    *[[:space:]]*)
        echo "refusing backup: credential paths must not contain whitespace" >&2
        exit 1
        ;;
esac

available_kib=$(df -Pk "$destination" | awk 'NR == 2 {print $4}')
case "$available_kib" in
    ''|*[!0-9]*)
        echo "could not determine remote backup free space" >&2
        exit 1
        ;;
esac
if [ "$available_kib" -lt "$minimum_free_kib" ]; then
    echo "refusing backup: remote target free-space floor not met" >&2
    exit 1
fi

if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "another MEPPP remote pull is already running" >&2
    exit 1
fi
run_id="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
incoming="${destination}/.incoming-${run_id}"
quarantine="${destination}/quarantine"
run_succeeded=0
case "$incoming" in
    "${destination}/.incoming-"*) ;;
    *) exit 1 ;;
esac
cleanup_run() {
    status=$?
    if [ -d "$incoming" ]; then
        if [ "$run_succeeded" -eq 1 ]; then
            rm -rf "$incoming"
        else
            evidence_dir="${quarantine}/failed-${run_id}"
            mkdir -p "$evidence_dir" 2>/dev/null || true
            for evidence_file in "$incoming"/meppp-*.sqlite3 "$incoming"/*.sha256; do
                if [ -f "$evidence_file" ] && [ ! -L "$evidence_file" ]; then
                    mv "$evidence_file" "$evidence_dir/" 2>/dev/null || true
                fi
            done
            printf 'failed_utc=%s\nexit_status=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$status" \
                > "${evidence_dir}/FAILURE" 2>/dev/null || true
            rm -rf "$incoming"
        fi
    fi
    rmdir "$lock_dir" 2>/dev/null || true
    trap - EXIT
    exit "$status"
}
trap cleanup_run EXIT
trap 'exit 1' HUP INT TERM

mkdir "$incoming"
mkdir "$incoming/media"

RSYNC_RSH="/usr/bin/ssh -i ${ssh_key} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${known_hosts} -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
export RSYNC_RSH

/usr/bin/rsync -rtp --timeout=120 \
    --include='meppp-*.sqlite3' \
    --include='meppp-*.sqlite3.sha256' \
    --include='meppp-*-media.sha256' \
    --exclude='*' \
    "${source_remote}:/backups/sqlite/" "$incoming/"
/usr/bin/rsync -rtp --timeout=120 \
    --include='*/' --include='*.webp' --include='*.mp4' --include='*.webm' --exclude='*' \
    "${source_remote}:/media/" "$incoming/media/"

if [ -n "$(find "$incoming/media" -type l -print -quit)" ]; then
    echo "refusing backup result: symbolic link found" >&2
    exit 1
fi
if [ -n "$(find "$incoming/media" -type f ! \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)" ]; then
    echo "refusing backup result: unexpected media file found" >&2
    exit 1
fi

latest=$(find "$incoming" -maxdepth 1 -name 'meppp-*.sqlite3' -type f | sort | tail -1)
test -n "$latest"
latest_name=$(basename "$latest")
snapshot_id=$(basename "$latest_name" .sqlite3)
database_manifest="${latest_name}.sha256"
media_manifest="${snapshot_id}-media.sha256"
test -f "${incoming}/${database_manifest}"
test -f "${incoming}/${media_manifest}"

python3 -c '
import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
database_name = sys.argv[2]
lines = manifest_path.read_text(encoding="ascii").splitlines()
expected = re.compile(r"[0-9a-f]{64}  " + re.escape(database_name))
if len(lines) != 1 or expected.fullmatch(lines[0]) is None:
    raise SystemExit("database manifest must contain exactly the selected snapshot")
' "${incoming}/${database_manifest}" "$latest_name"

previous_snapshot=""
if [ -f "${destination}/LAST_SUCCESS" ]; then
    test "$(grep -c '^snapshot=' "${destination}/LAST_SUCCESS")" = 1
    previous_snapshot=$(sed -n 's/^snapshot=//p' "${destination}/LAST_SUCCESS")
fi
snapshot_epoch=$(python3 -c '
import re
import sys
from datetime import datetime, timezone

pattern = r"meppp-(\d{8}T\d{6}\.\d{6}Z)\.sqlite3"
current, previous = sys.argv[1:]
match = re.fullmatch(pattern, current)
if match is None:
    raise SystemExit("invalid current snapshot name")
if previous and (re.fullmatch(pattern, previous) is None or current <= previous):
    raise SystemExit("source did not publish a strictly newer snapshot")
timestamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
print(int(timestamp.timestamp()))
' "$latest_name" "$previous_snapshot")

(
    cd "$incoming"
    sha256sum --check "$database_manifest"
)
restore_drill="${incoming}/restored.sqlite3"
python3 -c '
import sqlite3
import sys

source_path, destination_path = sys.argv[1:]
with sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True) as source:
    with sqlite3.connect(destination_path) as restored:
        source.backup(restored)
        rows = restored.execute("PRAGMA integrity_check").fetchall()
if rows != [("ok",)]:
    raise SystemExit("remote SQLite restore integrity check failed")
' "$latest" "$restore_drill"
rm -f "$restore_drill"

python3 -c '
import re
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
manifest = Path(sys.argv[2])
for line in manifest.read_text(encoding="ascii").splitlines():
    parts = line.split("  ", 1)
    if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None:
        raise SystemExit("invalid media manifest line")
    relative = PurePosixPath(parts[1])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("media", "entries") or relative.suffix not in {".webp", ".mp4", ".webm"}:
        raise SystemExit("unsafe media manifest path")
    candidate = root.joinpath(*relative.parts)
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit("media manifest file is missing or unsafe")
' "$incoming" "${incoming}/${media_manifest}"
if [ -s "${incoming}/${media_manifest}" ]; then
    (
        cd "$incoming"
        sha256sum --check "$media_manifest"
    )
else
    test -z "$(find "$incoming/media" -type f \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)"
fi

now_epoch=$(date +%s)
snapshot_age=$((now_epoch - snapshot_epoch))
if [ "$snapshot_age" -lt 0 ] || [ "$snapshot_age" -gt "$max_age_seconds" ]; then
    echo "remote backup is outside the allowed freshness window" >&2
    exit 1
fi

mkdir -p "$destination/media" "$destination/snapshots" "$quarantine"
if [ -n "$(find "$destination/media" -type l -print -quit)" ]; then
    echo "refusing backup: destination media contains a symbolic link" >&2
    exit 1
fi
if [ -n "$(find "$destination/media" -type f ! \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)" ]; then
    echo "refusing backup: destination media contains an unexpected file" >&2
    exit 1
fi
/usr/bin/rsync -rtp --ignore-existing "$incoming/media/" "$destination/media/"

publish_dir="${incoming}/publish"
final_snapshot_dir="${destination}/snapshots/${snapshot_id}"
if [ -L "$final_snapshot_dir" ]; then
    echo "refusing backup: final snapshot path is a symbolic link" >&2
    exit 1
fi
mkdir "$publish_dir"
mv "$latest" "${incoming}/${database_manifest}" "${incoming}/${media_manifest}" "$publish_dir/"
if [ -d "$final_snapshot_dir" ]; then
    cmp "${publish_dir}/${latest_name}" "${final_snapshot_dir}/${latest_name}"
    cmp "${publish_dir}/${database_manifest}" "${final_snapshot_dir}/${database_manifest}"
    cmp "${publish_dir}/${media_manifest}" "${final_snapshot_dir}/${media_manifest}"
else
    mv "$publish_dir" "$final_snapshot_dir"
fi
(
    cd "$final_snapshot_dir"
    sha256sum --check "$database_manifest"
)
if [ -s "${final_snapshot_dir}/${media_manifest}" ]; then
    (
        cd "$destination"
        sha256sum --check "snapshots/${snapshot_id}/${media_manifest}"
    )
else
    test -z "$(find "$destination/media" -type f \( -name '*.webp' -o -name '*.mp4' -o -name '*.webm' \) -print -quit)"
fi
sync -f "$destination"

success_tmp="${destination}/.LAST_SUCCESS.$$"
{
    printf 'completed_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'snapshot=%s\n' "$latest_name"
    printf 'snapshot_age_seconds=%s\n' "$snapshot_age"
    printf 'database_integrity=ok\n'
    printf 'media_manifest=%s\n' "$media_manifest"
    printf 'snapshot_path=snapshots/%s\n' "$snapshot_id"
} > "$success_tmp"
chmod 600 "$success_tmp"
mv "$success_tmp" "${destination}/LAST_SUCCESS"
rm -f "${destination}/LAST_FAILURE" "${destination}/MONITOR_FAILURE"
sync -f "$destination"
run_succeeded=1

echo "MEPPP remote pull, restore drill, and independent verification passed: ${latest_name}"
