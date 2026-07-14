"""
Records job-folder rename history so a resurrected old name can be recognized
even after the job's number itself changed.

find_job_number_collision (duplicate_job_guard.py) only catches two folders
sharing the same CURRENT job number - it cannot catch the actual incident that
motivated this plan: a rename that also changes the job number (e.g. 502 ->
649), after which a Syncthing peer resurrects a stale copy under the OLD name.
By the time that ghost reappears, nothing else on disk carries the old number
anymore, so the collision check alone finds nothing. This module closes that
gap by remembering "this exact folder name used to be a job, until it was
renamed to X" for a bounded window, independent of current job numbers.

History lives in local app state (BASE_DATA_DIR), not on the shared Ready Jobs
root, matching the existing convention for this kind of bookkeeping (see
tracker_bad_parts_state.json / pending_queue.json).
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Optional

from .atomic_write import atomic_write_json
from .config import BASE_DATA_DIR

main_logger = logging.getLogger("main")

RENAME_HISTORY_FILE = Path(BASE_DATA_DIR) / "rename_history.json"
RENAME_HISTORY_RETENTION_DAYS = 30


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _load_entries(history_file: Path) -> list:
    try:
        with history_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        main_logger.error("Failed reading rename history %s: %s", history_file, exc)
        return []


def _prune(entries: list) -> list:
    cutoff = _now() - datetime.timedelta(days=RENAME_HISTORY_RETENTION_DAYS)
    kept = []
    for entry in entries:
        try:
            renamed_at = datetime.datetime.fromisoformat(str(entry.get("renamedAt", "")))
        except ValueError:
            continue
        if renamed_at >= cutoff:
            kept.append(entry)
    return kept


def record_rename(old_name: str, new_name: str, *, history_file: Optional[Path] = None) -> None:
    path = Path(history_file) if history_file is not None else RENAME_HISTORY_FILE
    entries = _prune(_load_entries(path))
    entries.append({
        "oldName": old_name,
        "newName": new_name,
        "renamedAt": _now().isoformat(),
    })
    atomic_write_json(path, entries, indent=2, ensure_ascii=False)


def find_recent_rename_source(job_folder_name: str, *, history_file: Optional[Path] = None) -> Optional[dict]:
    """Return the most recent still-fresh rename entry whose old name is `job_folder_name`, if any."""
    path = Path(history_file) if history_file is not None else RENAME_HISTORY_FILE
    matches = [e for e in _prune(_load_entries(path)) if e.get("oldName") == job_folder_name]
    if not matches:
        return None
    return max(matches, key=lambda e: e.get("renamedAt", ""))
