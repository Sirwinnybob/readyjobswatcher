"""
Detects and marks suspected duplicate job folders.

A duplicate can appear when a job folder is renamed while the shared Ready Jobs
root is being synced by Syncthing across multiple devices: a peer that hasn't
yet observed the rename can push its own stale copy of the old-named folder
back onto the share. Ready Jobs Watcher's normal "a new job folder appeared"
handling has no way to tell that apart from a genuinely new job, so instead of
silently adopting it as a live job (creating a deployment_gate.json and
prompting an operator), this module lets the caller mark it for manual review.

The marker file (duplicate_suspect.json) is intentionally separate from
deployment_gate.json - that file's schema is a stable contract with the
Android app and must not gain new fields for this.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Optional

from .atomic_write import atomic_write_json

main_logger = logging.getLogger("main")

DUPLICATE_SUSPECT_FILENAME = "duplicate_suspect.json"


def _marker_path(root_dir: str, job_folder_name: str) -> Path:
    return Path(root_dir) / job_folder_name / ".metadata" / DUPLICATE_SUSPECT_FILENAME


def find_job_number_collision(root_dir: str, job_folder_name: str, job_num: str) -> Optional[str]:
    """Return the name of another top-level folder sharing `job_num`, if any."""
    if not job_num:
        return None
    from .file_handler import JobProcessor

    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name == job_folder_name:
                    continue
                if JobProcessor.extract_job_number(entry.name) == job_num:
                    return entry.name
    except OSError as exc:
        main_logger.error("Failed scanning %s for job-number collisions: %s", root_dir, exc)
        return None
    return None


def write_duplicate_suspect_marker(
    root_dir: str, job_folder_name: str, collided_with: str, *, reason: str = "job_number_collision"
) -> None:
    path = _marker_path(root_dir, job_folder_name)
    payload = {
        "schemaVersion": 1,
        "suspectedDuplicateOf": collided_with,
        "detectedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": reason,
    }
    atomic_write_json(path, payload, indent=2, ensure_ascii=False)


def read_duplicate_suspect_marker(root_dir: str, job_folder_name: str) -> Optional[dict]:
    path = _marker_path(root_dir, job_folder_name)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:
        main_logger.error("Failed reading duplicate suspect marker %s: %s", path, exc)
        return None


def clear_duplicate_suspect_marker(root_dir: str, job_folder_name: str) -> None:
    path = _marker_path(root_dir, job_folder_name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        main_logger.error("Failed clearing duplicate suspect marker %s: %s", path, exc)
