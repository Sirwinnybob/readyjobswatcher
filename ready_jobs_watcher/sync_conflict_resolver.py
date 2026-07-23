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

# How long to wait between the two stat() samples used to confirm a conflict
# file is no longer being actively written. Short enough not to noticeably
# stall a sweep over many files, long enough to catch an in-progress write.
_STABLE_CHECK_INTERVAL_SECONDS = 0.1


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
    - lives under a ``.metadata\\sync_conflicts`` or ``.stversions`` directory
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


def _is_stable_file(path: Path, interval: float = _STABLE_CHECK_INTERVAL_SECONDS) -> bool:
    """
    Sample (size, mtime_ns) twice across a short bounded interval to guard
    against processing a conflict file that Syncthing is still actively
    writing. Returns False (not stable) if the file vanishes or its
    signature changes between samples -- a later event/sweep will retry it
    once it settles. A stable empty file is still considered stable; a
    genuine empty file is valid content, not a sign of an in-progress write.
    """
    first = _stat_signature(path)
    if first is None:
        return False
    time.sleep(interval)
    second = _stat_signature(path)
    if second is None:
        return False
    return first == second


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
        return None
    if not conflict.is_file():
        return None
    if not _is_stable_file(conflict):
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
