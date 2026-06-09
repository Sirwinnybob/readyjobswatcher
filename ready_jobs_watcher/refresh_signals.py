"""
Shared refresh signal helpers.

These helpers write small JSON heartbeat files under tracker directories so
tablet apps can detect watcher-originated changes and refresh immediately.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import uuid
from typing import Optional


_CNC_TRACKER_RELATIVE = os.path.join("CNC", ".tracker")
_HARDWOODS_TRACKER_RELATIVE = os.path.join(".metadata", "hardwoods", ".tracker")
_WATCHER_SIGNAL_FILENAME = "watcher_refresh_watcher.json"


def _atomic_write_json(path: str, payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
    except OSError as exc:
        logging.warning("refresh_signals: could not write signal file %s: %s", path, exc)


def _signal_payload(reason: str, source: str, job_folder_name: str) -> dict:
    return {
        "source": source,
        "reason": reason,
        "jobFolderName": job_folder_name,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def touch_cnc_refresh_signal(job_folder_path: str, reason: str, source: str) -> Optional[str]:
    if not job_folder_path:
        return None
    job_folder_name = os.path.basename(os.path.normpath(job_folder_path))
    signal_path = os.path.join(job_folder_path, _CNC_TRACKER_RELATIVE, _WATCHER_SIGNAL_FILENAME)
    _atomic_write_json(signal_path, _signal_payload(reason=reason, source=source, job_folder_name=job_folder_name))
    return signal_path


def touch_hardwoods_refresh_signal(job_folder_path: str, reason: str, source: str) -> Optional[str]:
    if not job_folder_path:
        return None
    job_folder_name = os.path.basename(os.path.normpath(job_folder_path))
    signal_path = os.path.join(job_folder_path, _HARDWOODS_TRACKER_RELATIVE, _WATCHER_SIGNAL_FILENAME)
    _atomic_write_json(signal_path, _signal_payload(reason=reason, source=source, job_folder_name=job_folder_name))
    return signal_path
