"""
Tracker action stream loading helpers.

Supports both legacy ``.tracker/*.json`` tablet files and migrated
``.tracker/events/*.ndjson`` event streams.
"""
from __future__ import annotations

import json
import os
import glob
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .file_handler import JobProcessor

_CNC_OP_MAP = {
    "set_complete_true": "complete",
    "set_complete_false": "uncomplete",
    "set_skipped_true": "skip",
    "set_skipped_false": "unskip",
    "set_bad_part_true": "bad_part",
    "set_bad_part_false": "unbad_part",
    "bad_part_submitted": "bad_part_submitted",
    "view": "view",
}

_HARDWOODS_OPS = {
    "set_done_count",
    "set_bad_count",
    "set_skipped",
    "clear_skipped",
    "add_totals_rip10_done_count",
    "set_totals_rip10_done_count",
}


def load_cnc_tracker_actions(
    tracker_dir: str,
    logger=None,
) -> List[Dict[str, Any]]:
    return _load_tracker_actions(
        tracker_dirs=[tracker_dir],
        mapper=_map_cnc_event_to_action,
        logger=logger,
    )


def load_hardwoods_tracker_actions(
    tracker_dirs: Sequence[str],
    logger=None,
) -> List[Dict[str, Any]]:
    return _load_tracker_actions(
        tracker_dirs=tracker_dirs,
        mapper=_map_hardwoods_event_to_action,
        logger=logger,
    )


def _load_tracker_actions(
    tracker_dirs: Sequence[str],
    mapper: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    logger=None,
) -> List[Dict[str, Any]]:
    ndjson_files = _collect_ndjson_files(tracker_dirs)
    legacy_actions = _load_legacy_json_actions(tracker_dirs, logger=logger)
    if ndjson_files:
        migrated_actions = _load_migrated_event_actions(ndjson_files, mapper, logger=logger)
        return _sort_combined_actions(migrated_actions + legacy_actions)
    return legacy_actions


def _sort_combined_actions(actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        actions,
        key=lambda action: (
            str(action.get("timestamp", "") or ""),
            _coerce_int(action.get("_lamport")) or 0,
            str(action.get("_event_id", "") or ""),
            str(action.get("file", "") or ""),
            _coerce_int(action.get("page")) or 0,
            str(action.get("action", "") or ""),
        ),
    )


def _is_active_stream_file(name: str) -> bool:
    """AUD-05: single active-file predicate shared by the legacy JSON and NDJSON loaders.
    Excludes Syncthing conflict copies ('<name>.sync-conflict-<...>') and hidden/dot files
    case-insensitively. Replaying a conflict stream can resurrect stale CNC/hardwood progress
    or bad-part actions into consolidated state."""
    lowered = name.lower()
    if lowered.startswith("."):
        return False
    if ".sync-conflict-" in lowered:
        return False
    return True


def _collect_ndjson_files(tracker_dirs: Sequence[str]) -> List[str]:
    files: List[str] = []
    for tracker_dir in tracker_dirs:
        events_dir = os.path.join(tracker_dir, "events")
        if not os.path.isdir(events_dir):
            continue
        for path in sorted(glob.glob(os.path.join(events_dir, "**", "*.ndjson"), recursive=True)):
            if not os.path.isfile(path):
                continue
            # Apply the predicate to every path segment below events/ so a nested conflict
            # file OR a conflicted sub-directory is excluded, not just a flat filename.
            rel_parts = os.path.relpath(path, events_dir).replace("\\", "/").split("/")
            if all(_is_active_stream_file(part) for part in rel_parts):
                files.append(path)
    return files


def _load_migrated_event_actions(
    ndjson_files: Sequence[str],
    mapper: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    logger=None,
) -> List[Dict[str, Any]]:
    rows: List[Tuple[str, int, str, str, int, Dict[str, Any]]] = []
    for path in ndjson_files:
        try:
            for event_idx, payload in _iter_event_payloads(path, logger=logger):
                if not isinstance(payload, dict):
                    continue
                action = mapper(payload)
                if not isinstance(action, dict):
                    continue
                ts = str(action.get("timestamp", "") or "")
                lamport = _coerce_int(action.get("_lamport")) or 0
                event_id = str(action.get("_event_id", "") or "")
                rows.append((ts, lamport, event_id, path, event_idx, action))
        except Exception as exc:
            if logger is not None:
                logger.warning("Skipping malformed tracker NDJSON stream %s (%s)", path, exc)

    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    return [row[5] for row in rows]


def _iter_event_payloads(path: str, logger=None) -> List[Tuple[int, Dict[str, Any]]]:
    """
    Parse tracker event stream values in a tolerant way.

    Supports:
    - strict NDJSON (one object per line),
    - pretty-printed multiline JSON objects concatenated in a stream,
    - top-level JSON arrays of event objects.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    decoder = json.JSONDecoder()
    out: List[Tuple[int, Dict[str, Any]]] = []
    idx = 0
    event_idx = 0
    malformed_count = 0
    n = len(text)

    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break

        try:
            payload, end_idx = decoder.raw_decode(text, idx)
        except JSONDecodeError as exc:
            malformed_count += 1
            if logger is not None and malformed_count <= 3:
                line_no = text.count("\n", 0, idx) + 1
                logger.warning("Skipping malformed tracker NDJSON line %s:%s (%s)", path, line_no, exc)
            idx = _next_recovery_index(text, idx)
            if idx < 0:
                break
            continue

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    out.append((event_idx, item))
                    event_idx += 1
        elif isinstance(payload, dict):
            out.append((event_idx, payload))
            event_idx += 1

        idx = end_idx

    if logger is not None and malformed_count > 3:
        logger.warning(
            "Skipping malformed tracker NDJSON line %s (suppressed %s additional malformed fragments)",
            path,
            malformed_count - 3,
        )

    return out


def _next_recovery_index(text: str, current_idx: int) -> int:
    """
    Recovery cursor used after a malformed JSON fragment.
    Prefer moving to next newline boundary, then next object/array start.
    """
    next_newline = text.find("\n", current_idx + 1)
    next_obj = text.find("{", current_idx + 1)
    next_arr = text.find("[", current_idx + 1)

    candidates = []
    if next_newline != -1:
        candidates.append(next_newline + 1)
    if next_obj != -1:
        candidates.append(next_obj)
    if next_arr != -1:
        candidates.append(next_arr)
    if not candidates:
        return -1
    return min(candidates)


# Bound on how many parent directories to walk while looking for the enclosing job folder from
# a tracker_dir (e.g. "<job>/CNC/.tracker" is 2 levels down, "<job>/.metadata/hardwoods/.tracker"
# is 3). Generous relative to both known shapes so a future tracker path nested one level deeper
# still resolves, without walking indefinitely on an unexpected layout.
_JOB_FOLDER_SEARCH_DEPTH = 8


def _find_job_folder(tracker_dir: Path) -> Optional[Path]:
    current = tracker_dir.parent
    for _ in range(_JOB_FOLDER_SEARCH_DEPTH):
        try:
            if JobProcessor.is_job_folder(str(current)):
                return current
        except Exception:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _original_matches_tracker_dir(original: Path, tracker_dir: Path) -> bool:
    """
    True when a conflict manifest's derived original path belongs to this exact tracker_dir.

    Compares only the trailing two path components (case-insensitively), e.g. ("CNC", ".tracker")
    or ("hardwoods", ".tracker"), rather than the full path -- archived manifests on the live share
    record originalPath using whatever spelling Syncthing saw it under (both UNC
    "\\\\host\\share\\..." and mapped "Y:\\..." forms have been observed for the same file), so a
    full-path compare would spuriously fail even for a genuine match.
    """
    original_tail = tuple(part.lower() for part in original.parent.parts[-2:])
    tracker_tail = tuple(part.lower() for part in tracker_dir.parts[-2:])
    return len(tracker_tail) == 2 and original_tail == tracker_tail


def _load_recovered_conflict_rows(
    tracker_dir: Path,
    logger=None,
) -> List[Tuple[str, str, int, Dict[str, Any]]]:
    """
    Recover actions from archived divergent Syncthing conflicts belonging to tracker_dir.

    sync_conflict_resolver.py archives a genuinely divergent conflict copy under
    "<job>/.metadata/sync_conflicts/<archive_id>/" and never merges it back into the live file (by
    design -- it never overwrites original bytes). Without this, whatever actions only existed on
    the losing side of that conflict are gone from consolidation forever. This reads them back in
    as ordinary historical action rows, every pass -- no "already folded" marker. A marker written
    here would be unsafe: this reader is also called by read-only consumers (tracker_bad_parts.py,
    remake_candidates_indexer.py) that never persist anything, so one of them reading first could
    mark an archive done before metadata_cache.py's actual consolidation pass ever saw it, silently
    dropping the recovery. Re-reading every pass is cheap at realistic archive volumes and safe
    since the CNC/hardwoods merge functions are already idempotent over repeated historical rows.
    """
    job_folder = _find_job_folder(tracker_dir)
    if job_folder is None:
        return []

    sync_conflicts_root = job_folder / ".metadata" / "sync_conflicts"
    if not sync_conflicts_root.is_dir():
        return []

    rows: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for manifest_path in sorted(sync_conflicts_root.glob("*/manifest.json")):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            if logger is not None:
                logger.debug("Skipping unreadable sync-conflict manifest %s (%s)", manifest_path, exc)
            continue

        if not isinstance(manifest, dict) or manifest.get("action") != "archived_divergent":
            continue

        original_path = manifest.get("originalPath")
        archive_path = manifest.get("archivePath")
        if not isinstance(original_path, str) or not isinstance(archive_path, str):
            continue
        if not _original_matches_tracker_dir(Path(original_path), tracker_dir):
            continue

        archived_file = Path(archive_path)
        try:
            with open(archived_file, "r", encoding="utf-8") as f:
                archived_payload = json.load(f)
        except Exception as exc:
            if logger is not None:
                logger.debug("Skipping unreadable archived tracker file %s (%s)", archived_file, exc)
            continue

        if (
            not isinstance(archived_payload, dict)
            or not isinstance(archived_payload.get("tabletId"), str)
            or not isinstance(archived_payload.get("actions"), list)
        ):
            continue

        archived_path_str = str(archived_file)
        for idx, action in enumerate(archived_payload["actions"]):
            if not isinstance(action, dict):
                continue
            ts = str(action.get("timestamp", "") or "")
            rows.append((ts, archived_path_str, idx, action))

    return rows


def _load_legacy_json_actions(
    tracker_dirs: Sequence[str],
    logger=None,
) -> List[Dict[str, Any]]:
    rows: List[Tuple[str, str, int, Dict[str, Any]]] = []
    for tracker_dir in tracker_dirs:
        if not os.path.isdir(tracker_dir):
            continue
        for name in sorted(os.listdir(tracker_dir)):
            if not name.lower().endswith(".json"):
                continue
            if not _is_active_stream_file(name):
                continue
            path = os.path.join(tracker_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Skipping malformed tracker file %s (%s)", path, exc)
                continue
            if not isinstance(payload, dict):
                continue
            raw_actions = payload.get("actions")
            if not isinstance(raw_actions, list):
                continue
            for idx, action in enumerate(raw_actions):
                if not isinstance(action, dict):
                    continue
                ts = str(action.get("timestamp", "") or "")
                rows.append((ts, path, idx, action))

        rows.extend(_load_recovered_conflict_rows(Path(tracker_dir), logger=logger))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in rows]


def _map_cnc_event_to_action(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    op = str(event.get("op", "") or "").strip()
    action_name = _CNC_OP_MAP.get(op)
    if not action_name:
        return None

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    pdf = str(payload.get("file", "") or "").strip()
    page = _coerce_int(payload.get("page"))
    part = _coerce_int(payload.get("part"))
    fingerprint = str(payload.get("fileFingerprint", "") or "")
    timestamp = str(payload.get("timestamp", "") or "")
    if not timestamp:
        timestamp = str(event.get("wallTime", "") or "")
    re_nested = payload.get("reNested")

    if not pdf or page is None:
        return None

    out: Dict[str, Any] = {
        "file": pdf,
        "page": page,
        "action": action_name,
        "timestamp": timestamp,
        "fileFingerprint": fingerprint,
        "_lamport": _coerce_int(event.get("lamport")),
        "_event_id": str(event.get("eventId", "") or ""),
    }
    if part is not None:
        out["part"] = part
    if isinstance(re_nested, bool):
        out["reNested"] = re_nested
    return out


def _map_hardwoods_event_to_action(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    op = str(event.get("op", "") or "").strip()
    if op not in _HARDWOODS_OPS:
        return None

    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    value = _coerce_int(payload.get("value"))
    timestamp = str(payload.get("timestamp", "") or "")
    if not timestamp:
        timestamp = str(event.get("wallTime", "") or "")

    totals_key_value = payload.get("totalsKey")
    totals_key: Optional[str]
    if totals_key_value is None:
        totals_key = None
    else:
        text = str(totals_key_value).strip()
        totals_key = text if text else None

    out: Dict[str, Any] = {
        "docType": str(payload.get("docType", "") or ""),
        "rowId": str(payload.get("rowId", "") or ""),
        "totalsKey": totals_key,
        "value": value,
        "timestamp": timestamp,
        "action": op,
        "_lamport": _coerce_int(event.get("lamport")),
        "_event_id": str(event.get("eventId", "") or ""),
    }
    return out


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return None
    return None
