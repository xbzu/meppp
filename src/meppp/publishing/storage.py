from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage


class AtomicFileSystemStorage(FileSystemStorage):
    """Expose a media file only after its complete contents reach stable storage."""

    def _save(self, name, content):
        storage_root = Path(self.location).resolve()
        full_path = Path(self.path(name))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = full_path.parent.resolve()
        try:
            resolved_parent.relative_to(storage_root)
        except ValueError as error:
            raise SuspiciousFileOperation("generated media path escapes storage root") from error
        full_path = resolved_parent / full_path.name
        if self.directory_permissions_mode is not None:
            directory = full_path.parent
            while directory != storage_root:
                os.chmod(directory, self.directory_permissions_mode)
                directory = directory.parent
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".meppp-upload-",
            dir=full_path.parent,
        )
        temporary_path = Path(temporary_name)
        final_link_created = False
        try:
            with os.fdopen(descriptor, "wb") as destination:
                for chunk in content.chunks():
                    destination.write(chunk.encode() if isinstance(chunk, str) else chunk)
                destination.flush()
                os.fsync(destination.fileno())

            permissions = self.file_permissions_mode
            if permissions is not None:
                os.chmod(temporary_path, permissions)

            # A hard link makes the fully written inode visible atomically and
            # refuses an unexpected collision instead of overwriting evidence.
            os.link(temporary_path, full_path)
            final_link_created = True
            temporary_path.unlink()
            directory_descriptor = os.open(full_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except BaseException:
            if final_link_created:
                full_path.unlink(missing_ok=True)
                try:
                    directory_descriptor = os.open(full_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_descriptor)
                    finally:
                        os.close(directory_descriptor)
                except OSError:
                    pass
            raise
        finally:
            temporary_path.unlink(missing_ok=True)
        return full_path.relative_to(storage_root).as_posix()
