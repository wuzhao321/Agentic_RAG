"""Process locks shared by the CLI and the local ingestion service."""

import os
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(RuntimeError):
    pass


@contextmanager
def file_lock(path: Path):
    """Acquire a nonblocking lock; the operating system releases it on process exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LockBusyError(f"已有进程持有锁：{path.name}") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockBusyError(f"已有进程持有锁：{path.name}") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
