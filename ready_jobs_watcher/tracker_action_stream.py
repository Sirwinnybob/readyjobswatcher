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
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
