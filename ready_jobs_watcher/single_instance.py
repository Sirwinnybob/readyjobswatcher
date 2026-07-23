"""
Kernel-owned single-instance guard for Ready Jobs Watcher.

The previous single-instance check (the old Application.acquire_lock /
release_lock in main.py) treated a plain-text PID file under BASE_DATA_DIR
(ready_jobs_watcher.lock) as if it were the lock itself: it read the file,
asked Windows whether that PID was still a running python process, and if
not just deleted the file and wrote its own PID. Two processes racing
through that check at nearly the same time can both conclude the file is
stale/absent and both "win" - which is exactly how this watcher ended up
running twice on the same machine.

SingleInstanceGuard replaces that PID-file-as-lock approach with a real
OS-level primitive: a Win32 named mutex (CreateMutexW). Ownership of the
mutex handle is the sole authority for whether this process is the
singleton instance. The diagnostic PID file, if a path is provided, is
written only for human debugging *after* the mutex is already owned, and
is never read back to decide ownership - it is advisory text, nothing more.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
from pathlib import Path
from typing import Optional

from .atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

DEFAULT_MUTEX_NAME = "Global\\KKC_ReadyJobsWatcher_SingleInstance"

# Win32 error code returned by CreateMutexW (via GetLastError) when a mutex
# with the requested name already existed before this call.
ERROR_ALREADY_EXISTS = 183

_IS_WINDOWS = os.name == "nt"


class SingleInstanceGuard:
    """Kernel-owned single-instance guard backed by a Win32 named mutex.

    `name` should be a Win32 kernel object name (e.g. "Global\\..." to be
    visible across all sessions on the machine, or "Local\\..." to scope it
    to the current session - tests use "Local\\..." names so they don't
    require elevated privileges).

    `diagnostic_path`, if given, is a plain-text file that receives this
    process's PID once the mutex is owned. It exists purely so a human can
    see which PID currently holds the lock; it is never consulted to decide
    ownership.
    """

    def __init__(
        self,
        name: str = DEFAULT_MUTEX_NAME,
        diagnostic_path: Optional[Path] = None,
    ) -> None:
        self.name = name
        self.diagnostic_path = Path(diagnostic_path) if diagnostic_path is not None else None
        self._handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        """Attempt to become the singleton owner. Returns True iff this
        process now owns the named mutex."""
        if not _IS_WINDOWS:
            raise RuntimeError(
                "SingleInstanceGuard requires Windows (Win32 CreateMutexW)."
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, self.name)
        last_error = ctypes.get_last_error()

        if not handle:
            logger.error(
                "CreateMutexW failed for mutex %s (GetLastError=%s).",
                self.name,
                last_error,
            )
            return False

        if last_error == ERROR_ALREADY_EXISTS:
            # Another process already owns this mutex. We must not keep our
            # handle open - close it immediately rather than falling back to
            # any PID-file check.
            kernel32.CloseHandle(handle)
            logger.info(
                "Another instance already holds mutex %s; not acquiring.",
                self.name,
            )
            return False

        self._handle = handle
        self._kernel32 = kernel32
        self._write_diagnostic_pid()
        logger.info(
            "Acquired single-instance mutex %s (PID %s).", self.name, os.getpid()
        )
        return True

    def release(self) -> None:
        """Release this process's ownership of the mutex, if held."""
        if self._handle is None or self._kernel32 is None:
            return

        self._remove_diagnostic_pid_if_ours()

        try:
            self._kernel32.CloseHandle(self._handle)
        except Exception as exc:
            logger.warning(
                "Failed closing mutex handle for %s: %s", self.name, exc
            )
        finally:
            self._handle = None
            self._kernel32 = None

        logger.info("Released single-instance mutex %s.", self.name)

    def _write_diagnostic_pid(self) -> None:
        if self.diagnostic_path is None:
            return
        try:
            atomic_write_text(self.diagnostic_path, str(os.getpid()), encoding="ascii")
        except Exception as exc:
            logger.warning(
                "Failed writing diagnostic PID file %s: %s", self.diagnostic_path, exc
            )

    def _remove_diagnostic_pid_if_ours(self) -> None:
        if self.diagnostic_path is None:
            return
        try:
            content = self.diagnostic_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning(
                "Failed reading diagnostic PID file %s: %s", self.diagnostic_path, exc
            )
            return

        if content != str(os.getpid()):
            # Some other process has since written its own PID here (or the
            # file was repurposed) - never remove state we don't own.
            return

        try:
            self.diagnostic_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(
                "Failed removing diagnostic PID file %s: %s", self.diagnostic_path, exc
            )

    def __enter__(self) -> "SingleInstanceGuard":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
