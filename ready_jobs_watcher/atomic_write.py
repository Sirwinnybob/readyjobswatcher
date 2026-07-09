"""
Shared atomic file-write helpers for Ready Jobs Watcher.

Centralizes the "write to a temp file, then os.replace() onto the
destination" pattern that used to be duplicated (with a small but real
naming inconsistency) across roughly a dozen modules in this repo. See
METADATA_AUDIT.md R-04 ("Centralize each program's atomic-write... into one
helper per repo; one hardened writer removes drift").

Temp files are always named with a pid+uuid suffix
(f"{path.name}.{pid}.{uuid4().hex}.tmp") rather than a bare f"{path}.tmp".
A bare ".tmp" suffix is shared by every writer targeting the same
destination, so two concurrent writers can collide on the SAME temp
filename (one can truncate or delete the other's in-flight write) even
though the final os.replace() call itself is atomic. The pid+uuid suffix
makes each writer's temp file unique, closing that latent race. It still
ends in ".tmp", so the existing "is this a leftover temp file" detection
(metadata_inventory.classify_metadata_path, which already special-cases
".tmp." appearing mid-name) keeps working unmodified.

Callers with extra semantics beyond a plain atomic write are NOT forced
through the high-level atomic_write_* functions below; they compose the
lower-level write_temp_*/atomic_replace primitives instead so their extra
behavior isn't flattened away:
  - refresh_signals.py serializes concurrent renames onto the same
    destination with a per-path lock and retries the final os.replace() on
    a Windows WinError-5 (access denied) race. It calls write_temp_json()
    for the "produce a fully-written temp file" step, then keeps its own
    lock + retry loop around the final replace.
  - pending_queue.py layers backup-file rotation and a restore-from-backup
    rollback path on top of its atomic write, and is left as a self
    contained implementation (see its module for the reasoning).
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Union

PathLike = Union[str, "os.PathLike[str]"]


def _unique_temp_path(path: Path) -> Path:
    """Return a temp path colocated with `path` that no concurrent writer can collide on."""
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def atomic_replace(temp_path: PathLike, dest_path: PathLike) -> None:
    """Final step of an atomic write: rename `temp_path` onto `dest_path`.

    Thin, documented wrapper around os.replace() so call sites that need to
    add their own retry/locking around just the rename (e.g. refresh_signals)
    have a single obvious primitive to reach for.
    """
    os.replace(temp_path, dest_path)


def write_temp_bytes(path: PathLike, data: bytes) -> Path:
    """Write `data` to a new unique temp file next to `path` (no replace yet).

    Creates `path`'s parent directory if needed and returns the temp file
    path so the caller can perform (or retry) the final atomic_replace/
    os.replace itself.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _unique_temp_path(path)
    tmp_path.write_bytes(data)
    return tmp_path


def write_temp_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> Path:
    """Text variant of write_temp_bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _unique_temp_path(path)
    tmp_path.write_text(text, encoding=encoding)
    return tmp_path


def write_temp_json(
    path: PathLike,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    encoding: str = "utf-8",
) -> Path:
    """JSON variant of write_temp_bytes."""
    text = json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    return write_temp_text(path, text, encoding=encoding)


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """Atomically write `data` to `path` via a unique temp file + os.replace."""
    tmp_path = write_temp_bytes(path, data)
    atomic_replace(tmp_path, path)


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write `text` to `path` via a unique temp file + os.replace."""
    tmp_path = write_temp_text(path, text, encoding=encoding)
    atomic_replace(tmp_path, path)


def atomic_write_json(
    path: PathLike,
    payload: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    encoding: str = "utf-8",
) -> None:
    """Atomically write `payload` as JSON to `path` via a unique temp file + os.replace."""
    tmp_path = write_temp_json(
        path,
        payload,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
        encoding=encoding,
    )
    atomic_replace(tmp_path, path)


def atomic_copy(source: PathLike, destination: PathLike) -> None:
    """Atomically copy `source` onto `destination` via a unique temp file + os.replace."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _unique_temp_path(destination)
    shutil.copy2(source, tmp_path)
    atomic_replace(tmp_path, destination)
