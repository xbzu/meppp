from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MIN_DAILY_BACKUPS = 7
MIN_WEEKLY_BACKUPS = 4
BACKUP_PATTERN = re.compile(r"^meppp-(?P<stamp>\d{8}T\d{6}\.\d{6}Z)\.sqlite3$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


class SQLiteBackupError(RuntimeError):
    """Raised when a backup or recovery safety check fails."""


@dataclass(frozen=True)
class BackupArtifact:
    path: Path
    manifest_path: Path
    sha256: str
    size: int
    deleted_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RestoreResult:
    destination: Path
    sha256: str


def _validate_retention(daily: int, weekly: int) -> None:
    if daily < MIN_DAILY_BACKUPS:
        raise SQLiteBackupError(f"daily retention must be at least {MIN_DAILY_BACKUPS}")
    if weekly < MIN_WEEKLY_BACKUPS:
        raise SQLiteBackupError(f"weekly retention must be at least {MIN_WEEKLY_BACKUPS}")


def _readonly_connection(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    return sqlite3.connect(f"{path.resolve().as_uri()}?{query}", uri=True)


def _sidecar_paths(path: Path) -> tuple[Path, ...]:
    return tuple(path.with_name(f"{path.name}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES)


def _remove_sidecars(path: Path) -> None:
    for sidecar_path in _sidecar_paths(path):
        sidecar_path.unlink(missing_ok=True)


def _make_single_file_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    journal_mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    if journal_mode != ("delete",):
        raise SQLiteBackupError("could not convert SQLite artifact to single-file mode")


def _sync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_integrity(path: Path) -> None:
    try:
        with _readonly_connection(path, immutable=True) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as error:
        raise SQLiteBackupError(f"SQLite integrity check could not run for {path}") from error
    if rows != [("ok",)]:
        raise SQLiteBackupError(f"SQLite integrity check failed for {path}")


def manifest_path_for(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.sha256")


def _parse_manifest(manifest_path: Path, backup_name: str) -> str:
    try:
        lines = manifest_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise SQLiteBackupError(f"could not read checksum manifest {manifest_path}") from error
    if len(lines) != 1:
        raise SQLiteBackupError(f"invalid checksum manifest {manifest_path}")
    parts = lines[0].split("  ", maxsplit=1)
    if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]) or parts[1] != backup_name:
        raise SQLiteBackupError(f"invalid checksum manifest {manifest_path}")
    return parts[0]


def verify_manifest(backup_path: Path, manifest_path: Path | None = None) -> str:
    backup_path = backup_path.resolve()
    manifest_path = (manifest_path or manifest_path_for(backup_path)).resolve()
    if not backup_path.is_file():
        raise SQLiteBackupError(f"backup does not exist: {backup_path}")
    expected = _parse_manifest(manifest_path, backup_path.name)
    actual = sha256_file(backup_path)
    if not hmac.compare_digest(expected, actual):
        raise SQLiteBackupError(f"checksum mismatch for {backup_path}")
    return actual


def _write_manifest(backup_path: Path, sha256: str) -> Path:
    manifest_path = manifest_path_for(backup_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="ascii")
        descriptor = -1
        with handle:
            handle.write(f"{sha256}  {backup_path.name}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, manifest_path)
        _sync_directory(manifest_path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise
    return manifest_path


def _backup_timestamp(path: Path) -> datetime | None:
    match = BACKUP_PATTERN.fullmatch(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def prune_backups(
    backup_dir: Path, *, daily: int = MIN_DAILY_BACKUPS, weekly: int = MIN_WEEKLY_BACKUPS
) -> tuple[Path, ...]:
    """Keep the newest artifact in seven UTC-day and four ISO-week buckets."""
    _validate_retention(daily, weekly)
    candidates: list[tuple[datetime, Path, Path]] = []
    for backup_path in backup_dir.glob("meppp-*.sqlite3"):
        timestamp = _backup_timestamp(backup_path)
        manifest_path = manifest_path_for(backup_path)
        if timestamp is None or not manifest_path.is_file():
            continue
        try:
            verify_manifest(backup_path, manifest_path)
        except SQLiteBackupError:
            # Unknown or damaged artifacts are preserved for manual investigation.
            continue
        candidates.append((timestamp, backup_path, manifest_path))

    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    retained: set[Path] = set()
    daily_buckets: set[tuple[int, int]] = set()
    weekly_buckets: set[tuple[int, int]] = set()
    for timestamp, backup_path, _manifest_path in candidates:
        day_bucket = (timestamp.year, timestamp.timetuple().tm_yday)
        iso_year, iso_week, _iso_day = timestamp.isocalendar()
        week_bucket = (iso_year, iso_week)
        if day_bucket not in daily_buckets and len(daily_buckets) < daily:
            retained.add(backup_path)
            daily_buckets.add(day_bucket)
        if week_bucket not in weekly_buckets and len(weekly_buckets) < weekly:
            retained.add(backup_path)
            weekly_buckets.add(week_bucket)

    deleted: list[Path] = []
    for _timestamp, backup_path, manifest_path in candidates:
        if backup_path in retained:
            continue
        backup_path.unlink()
        manifest_path.unlink(missing_ok=True)
        deleted.extend((backup_path, manifest_path))
    if deleted:
        _sync_directory(backup_dir)
    return tuple(deleted)


def backup_database(
    database_path: Path,
    backup_dir: Path,
    *,
    daily: int = MIN_DAILY_BACKUPS,
    weekly: int = MIN_WEEKLY_BACKUPS,
    timestamp: datetime | None = None,
) -> BackupArtifact:
    """Create and verify an online SQLite backup, then apply bucket retention."""
    _validate_retention(daily, weekly)
    database_path = database_path.expanduser().resolve()
    backup_dir = backup_dir.expanduser().resolve()
    if not database_path.is_file():
        raise SQLiteBackupError(f"database does not exist: {database_path}")

    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = (timestamp or datetime.now(UTC)).astimezone(UTC)
    stamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = backup_dir / f"meppp-{stamp}.sqlite3"
    if backup_path.exists() or manifest_path_for(backup_path).exists():
        raise SQLiteBackupError(f"backup artifact already exists: {backup_path}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.", suffix=".tmp", dir=backup_dir
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with (
            _readonly_connection(database_path) as source,
            sqlite3.connect(temporary_path) as destination,
        ):
            source.backup(destination, pages=256, sleep=0.05)
            _make_single_file_database(destination)
        os.chmod(temporary_path, 0o600)
        assert_integrity(temporary_path)
        _sync_file(temporary_path)
        os.replace(temporary_path, backup_path)
        _sync_directory(backup_dir)
        sha256 = sha256_file(backup_path)
        manifest_path = _write_manifest(backup_path, sha256)
        deleted_paths = prune_backups(backup_dir, daily=daily, weekly=weekly)
    except (OSError, sqlite3.Error) as error:
        temporary_path.unlink(missing_ok=True)
        raise SQLiteBackupError(f"could not create SQLite backup for {database_path}") from error
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        _remove_sidecars(temporary_path)

    return BackupArtifact(
        path=backup_path,
        manifest_path=manifest_path,
        sha256=sha256,
        size=backup_path.stat().st_size,
        deleted_paths=deleted_paths,
    )


def restore_to_new_path(
    backup_path: Path, destination: Path, *, manifest_path: Path | None = None
) -> RestoreResult:
    """Restore into a path that must not exist; never overwrites a live database."""
    backup_path = backup_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise SQLiteBackupError(f"restore destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    verify_manifest(backup_path, manifest_path)
    assert_integrity(backup_path)
    try:
        with (
            _readonly_connection(backup_path, immutable=True) as source,
            sqlite3.connect(destination) as target,
        ):
            source.backup(target, pages=256, sleep=0.05)
            _make_single_file_database(target)
        os.chmod(destination, 0o600)
        assert_integrity(destination)
        _sync_file(destination)
        _sync_directory(destination.parent)
    except (OSError, sqlite3.Error) as error:
        destination.unlink(missing_ok=True)
        raise SQLiteBackupError(f"could not restore SQLite backup {backup_path}") from error
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        _remove_sidecars(destination)
    return RestoreResult(destination=destination, sha256=sha256_file(destination))
