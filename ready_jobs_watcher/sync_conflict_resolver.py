"""
Syncthing conflict resolver for Ready Jobs.

The resolver never overwrites an existing original with conflicting bytes. It
restores conflict files only when the original is missing, and otherwise moves
the conflict copy into a hidden per-job/root archive with a manifest.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .atomic_write import atomic_write_json as _shared_atomic_write_json


main_logger = logging.getLogger("main")

SYNC_CONFLICT_RE = re.compile(
    r"^(?P<stem>.+)\.sync-conflict-(?P<date>\d{8})-(?P<time>\d{6})-(?P<device>[^.]+)(?P<suffix>(?:\..+)?)$",
    re.IGNORECASE,
)

# Matches a trailing ".tmp" or ".tmp-<anything>" on the derived original name,
# e.g. Syncthing's own in-progress bookkeeping files.
_TMP_SUFFIX_RE = re.compile(r"\.tmp(-.*)?$", re.IGNORECASE)

# How long to wait between stat() samples used to confirm a conflict file is
# no longer being actively written, and how many samples to take. Short
# enough not to noticeably stall a sweep over many files, long enough (across
# multiple samples) to catch most in-progress writes -- see _is_stable_file's
# docstring for this heuristic's known limitation.
_STABLE_CHECK_INTERVAL_SECONDS = 0.1
_STABLE_CHECK_SAMPLES = 3

# Process-wide guard against two independent watchdog Observers (the rename
# handler's and the PDF handler's, both recursively watching the same tree)
# racing to resolve the exact same conflict file from two threads at once.
# Keyed on a normalized real path so equivalent spellings of the same file
# collapse to one key; a path already in flight is skipped, not queued.
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT_CONFLICT_PATHS: set[str] = set()


def _normalize_conflict_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


@dataclass(frozen=True)
class SyncConflictResolution:
    conflict_path: str
    original_path: str
    action: str
    archive_id: str = ""
    archive_path: str = ""


def is_sync_conflict_path(path: os.PathLike | str) -> bool:
    return SYNC_CONFLICT_RE.match(Path(path).name) is not None


def original_path_for_conflict(path: os.PathLike | str) -> Optional[Path]:
    conflict = Path(path)
    match = SYNC_CONFLICT_RE.match(conflict.name)
    if not match:
        return None
    return conflict.with_name(f"{match.group('stem')}{match.group('suffix')}")


def is_transient_conflict_path(path: os.PathLike | str) -> bool:
    """
    Return True when a sync-conflict path is Syncthing's own transient/internal
    bookkeeping rather than a genuine user-facing conflict.

    This is decided from the *derived original* path (the conflict filename
    with its ``.sync-conflict-<date>-<time>-<device>`` marker stripped back
    out), which is transient when it:
    - lives under a directory literally named ``sync_conflicts`` (in practice
      always nested under ``.metadata``, per ``_archive_root_for``) or
      ``.stversions``
    - is dot-prefixed/hidden (an internal-bookkeeping naming convention)
    - starts with ``.syncthing``
    - ends in ``.tmp`` or ``.tmp-<suffix>``
    """
    conflict = Path(path)
    original = original_path_for_conflict(conflict)
    if original is None:
        return False

    parts_lower = [part.lower() for part in original.parts]
    if "sync_conflicts" in parts_lower or ".stversions" in parts_lower:
        return True

    name = original.name
    name_lower = name.lower()
    if name.startswith("."):
        return True
    if name_lower.startswith(".syncthing"):
        return True
    if _TMP_SUFFIX_RE.search(name_lower):
        return True

    return False


def _stat_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (stat_result.st_size, stat_result.st_mtime_ns)


def _is_stable_file(
    path: Path,
    interval: float = _STABLE_CHECK_INTERVAL_SECONDS,
    samples: int = _STABLE_CHECK_SAMPLES,
) -> bool:
    """
    Sample (size, mtime_ns) across a short bounded window to guard against
    processing a conflict file that Syncthing is still actively writing.
    Returns False (not stable) if the file vanishes or its signature ever
    changes between samples -- a later event/sweep will retry it once it
    settles. A stable empty file is still considered stable; a genuine
    empty file is valid content, not a sign of an in-progress write.

    Known limitation: this is a best-effort heuristic, not a guarantee.
    Syncthing's puller can pre-allocate a destination file to its final
    size before filling in content, and mtime update granularity on a
    mapped network share (this watcher's real deployment target, Y:\\) can
    be coarser than this check's sampling window. A slow write that
    neither grows in size nor ticks mtime during the whole sampling window
    will read as "stable" even though it isn't. Re-sampling more than once
    narrows this window but cannot close it without a stronger signal
    (e.g. an OS-level file lock check), which is out of scope here.
    """
    previous = _stat_signature(path)
    if previous is None:
        return False
    for _ in range(samples - 1):
        time.sleep(interval)
        current = _stat_signature(path)
        if current is None or current != previous:
            return False
        previous = current
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_root_for(conflict: Path, ready_jobs_root: Path) -> Path:
    try:
        relative = conflict.resolve().relative_to(ready_jobs_root.resolve())
    except ValueError:
        return ready_jobs_root / ".metadata" / "sync_conflicts"

    parts = relative.parts
    if len(parts) >= 2 and not parts[0].startswith("."):
        return ready_jobs_root / parts[0] / ".metadata" / "sync_conflicts"
    return ready_jobs_root / ".metadata" / "sync_conflicts"


def _archive_id(conflict: Path) -> str:
    match = SYNC_CONFLICT_RE.match(conflict.name)
    if not match:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{timestamp}-unknown"
    return f"{match.group('date')}-{match.group('time')}-{match.group('device')}"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    base = path.with_suffix("")
    suffix = path.suffix
    counter = 2
    while True:
        candidate = Path(f"{base}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _move_preserving(conflict: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _unique_path(destination)
    shutil.move(str(conflict), str(destination))
    return destination


def _write_manifest(
    manifest_path: Path,
    *,
    conflict: Path,
    original: Path,
    archived: Path,
    action: str,
    same_content: bool,
    conflict_hash: str,
    original_hash: str,
) -> None:
    payload = {
        "resolvedAt": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "sameContent": same_content,
        "conflictPath": str(conflict),
        "originalPath": str(original),
        "archivePath": str(archived),
        "conflictSha256": conflict_hash,
        "originalSha256": original_hash,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = _unique_path(manifest_path)
    _shared_atomic_write_json(manifest_path, payload, indent=2, ensure_ascii=True, sort_keys=True)


def resolve_sync_conflict_file(
    conflict_path: os.PathLike | str,
    ready_jobs_root: os.PathLike | str,
) -> Optional[SyncConflictResolution]:
    conflict = Path(conflict_path)
    original = original_path_for_conflict(conflict)
    if original is None:
        return None
    if is_transient_conflict_path(conflict):
        main_logger.debug("Ignoring transient/internal sync-conflict path: %s", conflict)
        return None

    # Only conflicts that pass the (cheap, lock-free) transient check above
    # reach the in-flight guard below -- this is what actually closes the
    # RenameHandler-vs-PdfChangeHandler race, since both observers' threads
    # can otherwise reach this point for the same genuine conflict file at
    # nearly the same time.
    key = _normalize_conflict_key(conflict)
    with _IN_FLIGHT_LOCK:
        if key in _IN_FLIGHT_CONFLICT_PATHS:
            main_logger.debug(
                "Sync-conflict already being resolved by another thread, skipping: %s", conflict
            )
            return None
        _IN_FLIGHT_CONFLICT_PATHS.add(key)

    try:
        if not conflict.is_file():
            return None
        if not _is_stable_file(conflict):
            main_logger.debug(
                "Sync-conflict file not yet stable, deferring to a later sweep/event: %s", conflict
            )
            return None

        root = Path(ready_jobs_root)
        if not original.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            restored = _move_preserving(conflict, original)
            main_logger.warning("Restored Syncthing conflict because original was missing: %s -> %s", conflict, restored)
            return SyncConflictResolution(
                conflict_path=str(conflict),
                original_path=str(restored),
                action="restored_missing_original",
            )

        conflict_hash = _sha256(conflict)
        original_hash = _sha256(original)
        same_content = conflict_hash == original_hash
        action = "archived_duplicate" if same_content else "archived_divergent"
        archive_id = _archive_id(conflict)
        archive_dir = _archive_root_for(conflict, root) / archive_id
        archived = _move_preserving(conflict, archive_dir / original.name)
        _write_manifest(
            archive_dir / "manifest.json",
            conflict=conflict,
            original=original,
            archived=archived,
            action=action,
            same_content=same_content,
            conflict_hash=conflict_hash,
            original_hash=original_hash,
        )
        main_logger.warning("Archived Syncthing conflict (%s): %s -> %s", action, conflict, archived)
        return SyncConflictResolution(
            conflict_path=str(conflict),
            original_path=str(original),
            action=action,
            archive_id=archive_id,
            archive_path=str(archived),
        )
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT_CONFLICT_PATHS.discard(key)


def _iter_conflicts(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name.lower() not in {".stversions"} and name.lower() != "sync_conflicts"
        ]
        for filename in filenames:
            if not SYNC_CONFLICT_RE.match(filename):
                continue
            candidate = Path(dirpath) / filename
            if is_transient_conflict_path(candidate):
                continue
            yield candidate


def scan_and_resolve_sync_conflicts(ready_jobs_root: os.PathLike | str) -> List[SyncConflictResolution]:
    root = Path(ready_jobs_root)
    if not root.is_dir():
        return []
    results: List[SyncConflictResolution] = []
    for conflict in list(_iter_conflicts(root)):
        try:
            result = resolve_sync_conflict_file(conflict, root)
            if result is not None:
                results.append(result)
        except Exception as exc:
            main_logger.error("Failed resolving Syncthing conflict %s: %s", conflict, exc, exc_info=True)
    return results
