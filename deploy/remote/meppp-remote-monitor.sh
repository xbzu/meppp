#!/bin/sh
set -eu
umask 077

destination="${MEPPP_BACKUP_DESTINATION:?Set MEPPP_BACKUP_DESTINATION}"
max_age_seconds="${MEPPP_BACKUP_MAX_AGE_SECONDS:-93600}"
success_marker="${destination}/LAST_SUCCESS"

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
        echo "remote backup path is a symbolic link" >&2
        exit 1
    fi
done
test -f "$success_marker"
test ! -L "$success_marker"
test ! -e "${destination}/LAST_FAILURE"
test -d "${destination}/media"
if [ -n "$(find "${destination}/media" -type l -print -quit)" ]; then
    echo "remote backup media contains a symbolic link" >&2
    exit 1
fi

python3 -c '
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

destination = Path(sys.argv[1]).resolve()
max_age = int(sys.argv[2])
marker = destination / "LAST_SUCCESS"
fields = {}
for line in marker.read_text(encoding="ascii").splitlines():
    key, separator, value = line.partition("=")
    if not separator or key in fields:
        raise SystemExit("invalid or duplicate LAST_SUCCESS field")
    fields[key] = value

required = {
    "completed_utc",
    "snapshot",
    "snapshot_age_seconds",
    "database_integrity",
    "media_manifest",
    "snapshot_path",
}
if set(fields) != required or fields["database_integrity"] != "ok":
    raise SystemExit("LAST_SUCCESS is incomplete")

completed = datetime.strptime(fields["completed_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
now = datetime.now(timezone.utc)
success_age = int((now - completed).total_seconds())
if success_age < 0 or success_age > max_age:
    raise SystemExit("remote backup success marker is stale")

snapshot = fields["snapshot"]
match = re.fullmatch(r"meppp-(\d{8}T\d{6}\.\d{6}Z)\.sqlite3", snapshot)
if match is None:
    raise SystemExit("invalid snapshot name")
snapshot_id = snapshot[: -len(".sqlite3")]
snapshot_created = datetime.strptime(
    match.group(1), "%Y%m%dT%H%M%S.%fZ"
).replace(tzinfo=timezone.utc)
snapshot_age = int((now - snapshot_created).total_seconds())
reported_snapshot_age = int(fields["snapshot_age_seconds"])
if (
    snapshot_age < 0
    or snapshot_age > max_age
    or reported_snapshot_age < 0
    or reported_snapshot_age > max_age
):
    raise SystemExit("remote backup snapshot is stale")
if fields["snapshot_path"] != f"snapshots/{snapshot_id}":
    raise SystemExit("snapshot path does not match snapshot")
if fields["media_manifest"] != f"{snapshot_id}-media.sha256":
    raise SystemExit("media manifest does not match snapshot")

snapshot_path = PurePosixPath(fields["snapshot_path"])
if snapshot_path.is_absolute() or ".." in snapshot_path.parts:
    raise SystemExit("unsafe snapshot path")
snapshot_dir = destination.joinpath(*snapshot_path.parts)
for path in (
    snapshot_dir,
    snapshot_dir / snapshot,
    snapshot_dir / f"{snapshot}.sha256",
    snapshot_dir / fields["media_manifest"],
):
    if (not path.exists()) or path.is_symlink():
        raise SystemExit(f"missing or unsafe backup artifact: {path.name}")

database_lines = (snapshot_dir / f"{snapshot}.sha256").read_text(encoding="ascii").splitlines()
expected_database = re.compile(r"[0-9a-f]{64}  " + re.escape(snapshot))
if len(database_lines) != 1 or expected_database.fullmatch(database_lines[0]) is None:
    raise SystemExit("invalid database manifest")

media_manifest = snapshot_dir / fields["media_manifest"]
for line in media_manifest.read_text(encoding="ascii").splitlines():
    parts = line.split("  ", 1)
    if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None:
        raise SystemExit("invalid media manifest line")
    relative = PurePosixPath(parts[1])
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("media", "entries") or relative.suffix != ".webp":
        raise SystemExit("unsafe media manifest path")
    media_path = destination.joinpath(*relative.parts)
    if not media_path.is_file() or media_path.is_symlink():
        raise SystemExit("media manifest artifact is missing or unsafe")

print(f"snapshot={snapshot}")
print(f"success_age_seconds={success_age}")
print(f"snapshot_age_seconds={snapshot_age}")
' "$destination" "$max_age_seconds"

snapshot_name=$(sed -n 's/^snapshot=//p' "$success_marker")
snapshot_id=${snapshot_name%.sqlite3}
snapshot_dir="${destination}/snapshots/${snapshot_id}"
database_manifest="${snapshot_name}.sha256"
media_manifest="${snapshot_id}-media.sha256"
(
    cd "$snapshot_dir"
    sha256sum --check "$database_manifest"
)
if [ -s "${snapshot_dir}/${media_manifest}" ]; then
    (
        cd "$destination"
        sha256sum --check "snapshots/${snapshot_id}/${media_manifest}"
    )
fi

rm -f "${destination}/MONITOR_FAILURE"
echo "MEPPP remote backup freshness and manifests are healthy: ${snapshot_name}"
