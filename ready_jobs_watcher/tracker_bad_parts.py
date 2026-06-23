"""
Tracker-based bad parts monitoring.

This module reads CNC tracker action streams and derives active bad-part events.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .config import BASE_DATA_DIR, Config
from .file_handler import JobProcessor
from .tracker_action_stream import load_cnc_tracker_actions

badparts_logger = logging.getLogger("badparts")

TRACKER_STATE_FILE = os.path.join(BASE_DATA_DIR, "tracker_bad_parts_state.json")

# Bad-part actions written before this ISO timestamp are treated as pre-submitted for
# backwards compatibility — they alert exactly as before the pending-submission feature.
# Update this to the actual deployment date when rolling out the new submit workflow.
SUBMISSION_REQUIRED_AFTER = "2026-05-21T00:00:00+00:00"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TrackerBadPartKey:
    job_folder_name: str
    pdf_filename: str
    page: int
    file_fingerprint: str
    part_number: int

    def to_token(self) -> str:
        return json.dumps(
            [
                self.job_folder_name,
                self.pdf_filename,
                self.page,
                self.file_fingerprint,
                self.part_number,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def from_token(token: str) -> "TrackerBadPartKey":
        job_folder_name, pdf_filename, page, file_fingerprint, part_number = json.loads(token)
        return TrackerBadPartKey(
            job_folder_name=str(job_folder_name),
            pdf_filename=str(pdf_filename),
            page=int(page),
            file_fingerprint=str(file_fingerprint),
            part_number=int(part_number),
        )


@dataclass(frozen=True)
class TrackerBadPartEvent:
    key: TrackerBadPartKey
    material_or_pdf: str
    detected_at: str


@dataclass(frozen=True)
class BadPartDetailRecord:
    key: TrackerBadPartKey
    token: str
    is_acknowledged: bool
    material: str
    pdf_filename: str
    pdf_full_path: str
    page: int
    part_number: int
    part_name: str
    width: Optional[float]
    length: Optional[float]
    cabinet_number: Optional[int]
    room: Optional[str]
    detected_at: str
    thumbnail_path: Optional[str]
    highlight_rect: Optional[Tuple[int, int, int, int]]


@dataclass
class TrackerBadPartState:
    active_keys: Set[str] = field(default_factory=set)
    seen_keys: Set[str] = field(default_factory=set)
    acknowledged_keys: Set[str] = field(default_factory=set)
    updated_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "active_keys": sorted(self.active_keys),
            "seen_keys": sorted(self.seen_keys),
            "acknowledged_keys": sorted(self.acknowledged_keys),
            "updated_at": self.updated_at or _now_iso(),
        }

    @staticmethod
    def from_dict(raw: Dict[str, object]) -> "TrackerBadPartState":
        def _as_set(name: str) -> Set[str]:
            value = raw.get(name, [])
            if not isinstance(value, list):
                return set()
            return {str(v) for v in value}

        return TrackerBadPartState(
            active_keys=_as_set("active_keys"),
            seen_keys=_as_set("seen_keys"),
            acknowledged_keys=_as_set("acknowledged_keys"),
            updated_at=str(raw.get("updated_at", "") or ""),
        )


class TrackerBadPartsMonitor:
    """
    Single-source bad-parts monitor based on CNC .tracker action streams.
    """

    def __init__(self, config: Config, state_file: str = TRACKER_STATE_FILE, deployment_gate=None):
        self.config = config
        self.state_file = state_file
        self.deployment_gate = deployment_gate
        self._lock = threading.Lock()
        self._material_cache: Dict[Tuple[str, str], str] = {}
        self._metadata_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self.state = self._load_state()

    def _load_state(self) -> TrackerBadPartState:
        try:
            if not os.path.exists(self.state_file):
                return TrackerBadPartState(updated_at=_now_iso())
            with open(self.state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return TrackerBadPartState(updated_at=_now_iso())
            return TrackerBadPartState.from_dict(raw)
        except Exception as exc:
            badparts_logger.error(f"Failed to load tracker bad-parts state: {exc}")
            return TrackerBadPartState(updated_at=_now_iso())

    def _save_state(self) -> None:
        self.state.updated_at = _now_iso()
        temp_path = f"{self.state_file}.tmp"
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        os.rename(temp_path, self.state_file)

    def _iter_job_folders(self) -> Iterable[Tuple[str, str]]:
        root_dir = self.config.ROOT_DIR
        if not os.path.isdir(root_dir):
            return []

        folders: List[Tuple[str, str]] = []
        try:
            with os.scandir(root_dir) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    job_folder_path = entry.path
                    if JobProcessor.is_job_folder(job_folder_path):
                        if self.deployment_gate is not None and not self.deployment_gate.should_process_job_folder(job_folder_path):
                            continue
                        folders.append((entry.name, job_folder_path))
        except OSError as exc:
            badparts_logger.error(f"Failed to scan root directory for tracker folders: {exc}")
        return folders

    def _iter_tracker_files(self) -> Iterable[Tuple[str, str]]:
        for job_folder_name, job_folder_path in self._iter_job_folders():
            tracker_dir = os.path.join(job_folder_path, self.config.CNC_SUBDIR, ".tracker")
            if not os.path.isdir(tracker_dir):
                continue
            try:
                with os.scandir(tracker_dir) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.lower().endswith(".json"):
                            yield job_folder_name, entry.path
            except OSError as exc:
                badparts_logger.error(f"Failed reading tracker directory {tracker_dir}: {exc}")

    def _load_material_name(self, key: TrackerBadPartKey) -> str:
        cache_key = (key.job_folder_name, key.pdf_filename)
        if cache_key in self._material_cache:
            return self._material_cache[cache_key]

        meta_path = self._metadata_path_for_key(key)
        material = key.pdf_filename
        try:
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    material = str(raw.get("material") or raw.get("pdfFilename") or material)
        except Exception:
            material = key.pdf_filename

        self._material_cache[cache_key] = material
        return material

    def _metadata_path_for_key(self, key: TrackerBadPartKey) -> str:
        meta_name = os.path.splitext(key.pdf_filename)[0] + ".json"
        return os.path.join(
            self.config.ROOT_DIR,
            key.job_folder_name,
            self.config.CNC_SUBDIR,
            ".metadata",
            meta_name,
        )

    def _load_metadata_for_key(self, key: TrackerBadPartKey) -> Optional[Dict[str, Any]]:
        cache_key = (key.job_folder_name, key.pdf_filename)
        if cache_key in self._metadata_cache:
            return self._metadata_cache[cache_key]

        meta_path = self._metadata_path_for_key(key)
        metadata: Optional[Dict[str, Any]] = None
        try:
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    metadata = raw
        except Exception:
            metadata = None

        self._metadata_cache[cache_key] = metadata
        return metadata

    def _resolve_page_metadata(self, metadata: Dict[str, Any], page: int) -> Optional[Dict[str, Any]]:
        pages = metadata.get("pages", [])
        if not isinstance(pages, list):
            return None
        for page_meta in pages:
            if not isinstance(page_meta, dict):
                continue
            if page_meta.get("pageNumber") == page:
                return page_meta
        return None

    @staticmethod
    def _extract_highlight_rect(page_meta: Dict[str, Any], part_number: int) -> Optional[Tuple[int, int, int, int]]:
        ocr_boxes = page_meta.get("ocrBoxes")
        if not isinstance(ocr_boxes, dict):
            return None
        part_boxes = ocr_boxes.get(str(part_number))
        if not isinstance(part_boxes, list) or not part_boxes:
            return None
        first_box = part_boxes[0]
        if not isinstance(first_box, dict):
            return None
        try:
            left = int(first_box.get("left"))
            top = int(first_box.get("top"))
            right = int(first_box.get("right"))
            bottom = int(first_box.get("bottom"))
        except (TypeError, ValueError):
            return None
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)

    def _resolve_thumbnail_path(self, key: TrackerBadPartKey, page_meta: Dict[str, Any]) -> Optional[str]:
        thumbnail_rel = page_meta.get("thumbnailPath")
        if not isinstance(thumbnail_rel, str) or not thumbnail_rel.strip():
            return None
        path = os.path.normpath(
            os.path.join(
                self.config.ROOT_DIR,
                key.job_folder_name,
                self.config.CNC_SUBDIR,
                thumbnail_rel.replace("/", os.sep),
            )
        )
        if os.path.exists(path):
            return path
        return None

    def _build_detail_record(
        self,
        key: TrackerBadPartKey,
        detected_at: str,
        is_acknowledged: bool,
    ) -> BadPartDetailRecord:
        material = self._load_material_name(key)
        part_name = f"Part {key.part_number}"
        width: Optional[float] = None
        length: Optional[float] = None
        cabinet_number: Optional[int] = None
        room: Optional[str] = None
        thumbnail_path: Optional[str] = None
        highlight_rect: Optional[Tuple[int, int, int, int]] = None

        metadata = self._load_metadata_for_key(key)
        if metadata:
            page_meta = self._resolve_page_metadata(metadata, key.page)
            if page_meta:
                parts = page_meta.get("parts", [])
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if part.get("number") != key.part_number:
                            continue
                        part_name = str(part.get("name") or part_name)
                        try:
                            width = float(part["width"]) if part.get("width") is not None else None
                        except (TypeError, ValueError):
                            width = None
                        try:
                            length = float(part["length"]) if part.get("length") is not None else None
                        except (TypeError, ValueError):
                            length = None
                        cab_value = part.get("cabNumber")
                        if isinstance(cab_value, int):
                            cabinet_number = cab_value
                        room_value = part.get("room")
                        if isinstance(room_value, str) and room_value.strip():
                            room = room_value.strip()
                        break

                thumbnail_path = self._resolve_thumbnail_path(key, page_meta)
                highlight_rect = self._extract_highlight_rect(page_meta, key.part_number)

        pdf_full_path = os.path.normpath(
            os.path.join(
                self.config.ROOT_DIR,
                key.job_folder_name,
                self.config.CNC_SUBDIR,
                key.pdf_filename,
            )
        )

        return BadPartDetailRecord(
            key=key,
            token=key.to_token(),
            is_acknowledged=is_acknowledged,
            material=material,
            pdf_filename=key.pdf_filename,
            pdf_full_path=pdf_full_path,
            page=key.page,
            part_number=key.part_number,
            part_name=part_name,
            width=width,
            length=length,
            cabinet_number=cabinet_number,
            room=room,
            detected_at=detected_at,
            thumbnail_path=thumbnail_path,
            highlight_rect=highlight_rect,
        )

    def get_detail_record(
        self,
        key: TrackerBadPartKey,
        detected_at: str = "",
        is_acknowledged: bool = False,
    ) -> BadPartDetailRecord:
        with self._lock:
            return self._build_detail_record(
                key=key,
                detected_at=detected_at,
                is_acknowledged=is_acknowledged,
            )

    def _collect_active_events_with_reactivations(self) -> Tuple[Dict[str, TrackerBadPartEvent], Set[str]]:
        action_rows: List[Tuple[str, str, int, Dict[str, object]]] = []
        for job_folder_name, job_folder_path in self._iter_job_folders():
            tracker_dir = os.path.join(job_folder_path, self.config.CNC_SUBDIR, ".tracker")
            if not os.path.isdir(tracker_dir):
                continue
            actions = load_cnc_tracker_actions(tracker_dir, logger=badparts_logger)
            for idx, action in enumerate(actions):
                timestamp = str(action.get("timestamp", "") or "")
                action_rows.append((timestamp, job_folder_name, idx, {"jobFolderName": job_folder_name, **action}))

        action_rows.sort(key=lambda row: (row[0], row[1], row[2]))

        status_map: Dict[str, bool] = {}
        event_map: Dict[str, TrackerBadPartEvent] = {}
        reactivated_tokens: Set[str] = set()
        # Tracks tokens whose bad_part has been explicitly submitted for engineer notification.
        # Only submitted tokens appear as active_events and trigger alerts.
        submitted_tokens: Set[str] = set()
        for _, _, _, action in action_rows:
            action_name = str(action.get("action", "") or "").strip().lower()
            if action_name not in ("bad_part", "unbad_part", "bad_part_submitted"):
                continue

            pdf = str(action.get("file", "") or "").strip()
            page = action.get("page")
            part = action.get("part")
            job_folder_name = str(action.get("jobFolderName", "") or "").strip()
            if not pdf or not job_folder_name:
                continue
            if not isinstance(page, int) or not isinstance(part, int):
                continue

            file_fingerprint = str(action.get("fileFingerprint", "") or "").strip() or "__legacy__"
            key = TrackerBadPartKey(
                job_folder_name=job_folder_name,
                pdf_filename=pdf,
                page=page,
                file_fingerprint=file_fingerprint,
                part_number=part,
            )
            token = key.to_token()

            if action_name == "bad_part":
                if token in status_map and status_map[token] is False:
                    reactivated_tokens.add(token)
                    submitted_tokens.discard(token)  # reset submission on reactivation
                status_map[token] = True
                action_ts = str(action.get("timestamp", "") or "")
                detected_at = action_ts
                event_map[token] = TrackerBadPartEvent(
                    key=key,
                    material_or_pdf=self._load_material_name(key),
                    detected_at=detected_at,
                )
                # Backwards compat: bad parts written before the cutover date are treated as
                # pre-submitted so they alert exactly as they did before this feature was added.
                if action_ts < SUBMISSION_REQUIRED_AFTER:
                    submitted_tokens.add(token)
            elif action_name == "unbad_part":
                status_map[token] = False
                submitted_tokens.discard(token)
            elif action_name == "bad_part_submitted":
                submitted_tokens.add(token)

        active_events: Dict[str, TrackerBadPartEvent] = {}
        for token, is_active in status_map.items():
            if not is_active:
                continue
            if token not in submitted_tokens:
                continue  # pending — operator has not tapped the Report button yet
            event = event_map.get(token)
            if event is not None:
                active_events[token] = event
        return active_events, reactivated_tokens

    def _collect_active_events(self) -> Dict[str, TrackerBadPartEvent]:
        active_events, _ = self._collect_active_events_with_reactivations()
        return active_events

    def scan_once(self) -> List[TrackerBadPartEvent]:
        """
        Rebuild active tracker bad-parts and return newly activated events.
        """
        with self._lock:
            active_events, reactivated_tokens = self._collect_active_events_with_reactivations()
            active_tokens = set(active_events.keys())

            resolved_tokens = self.state.active_keys - active_tokens
            newly_active_tokens = active_tokens - self.state.active_keys

            for token in sorted(resolved_tokens):
                key = TrackerBadPartKey.from_token(token)
                badparts_logger.info(
                    "TRACKER_RESOLVED job=%s pdf=%s page=%s part=%s fp=%s",
                    key.job_folder_name,
                    key.pdf_filename,
                    key.page,
                    key.part_number,
                    key.file_fingerprint,
                )

            if resolved_tokens:
                self.state.acknowledged_keys -= resolved_tokens

            new_events: List[TrackerBadPartEvent] = []
            for token in sorted(newly_active_tokens):
                if token in self.state.acknowledged_keys:
                    continue
                event = active_events.get(token)
                if event is None:
                    continue
                new_events.append(event)
                badparts_logger.warning(
                    "TRACKER_NEW job=%s pdf=%s page=%s part=%s fp=%s",
                    event.key.job_folder_name,
                    event.key.pdf_filename,
                    event.key.page,
                    event.key.part_number,
                    event.key.file_fingerprint,
                )

            new_event_tokens = {event.key.to_token() for event in new_events}
            reactivated_alert_tokens = (active_tokens & self.state.acknowledged_keys & reactivated_tokens) - new_event_tokens
            for token in sorted(reactivated_alert_tokens):
                event = active_events.get(token)
                if event is None:
                    continue
                self.state.acknowledged_keys.discard(token)
                new_events.append(event)
                badparts_logger.warning(
                    "TRACKER_REACTIVATED job=%s pdf=%s page=%s part=%s fp=%s",
                    event.key.job_folder_name,
                    event.key.pdf_filename,
                    event.key.page,
                    event.key.part_number,
                    event.key.file_fingerprint,
                )

            self.state.active_keys = active_tokens
            self.state.seen_keys |= active_tokens
            self._save_state()
            return new_events

    def acknowledge_keys(self, keys: Iterable[TrackerBadPartKey]) -> int:
        with self._lock:
            tokens = {key.to_token() for key in keys}
            if not tokens:
                return 0
            self.state.acknowledged_keys |= tokens
            self._save_state()
            for token in sorted(tokens):
                key = TrackerBadPartKey.from_token(token)
                badparts_logger.info(
                    "TRACKER_ACK job=%s pdf=%s page=%s part=%s fp=%s",
                    key.job_folder_name,
                    key.pdf_filename,
                    key.page,
                    key.part_number,
                    key.file_fingerprint,
                )
            return len(tokens)

    def unacknowledge_keys(self, keys: Iterable[TrackerBadPartKey]) -> int:
        with self._lock:
            tokens = {key.to_token() for key in keys}
            if not tokens:
                return 0
            removed = self.state.acknowledged_keys & tokens
            if not removed:
                return 0
            self.state.acknowledged_keys -= removed
            self._save_state()
            for token in sorted(removed):
                key = TrackerBadPartKey.from_token(token)
                badparts_logger.info(
                    "TRACKER_UNACK job=%s pdf=%s page=%s part=%s fp=%s",
                    key.job_folder_name,
                    key.pdf_filename,
                    key.page,
                    key.part_number,
                    key.file_fingerprint,
                )
            return len(removed)

    def rename_job_folder(self, old_name: str, new_name: str, old_num: str, new_num: str) -> None:
        """
        Propagates metadata updates when a top-level job folder is renamed.
        Updates active_keys, seen_keys, and acknowledged_keys in the monitor's state.
        """
        with self._lock:
            self.state = self._load_state()
            for set_name in ("active_keys", "seen_keys", "acknowledged_keys"):
                current_set = getattr(self.state, set_name)
                updated_set = set()
                for token in current_set:
                    try:
                        key = TrackerBadPartKey.from_token(token)
                    except Exception:
                        updated_set.add(token)
                        continue

                    if key.job_folder_name == old_name:
                        pdf_filename = key.pdf_filename
                        if new_num:
                            if old_num and pdf_filename.startswith(old_num + " - "):
                                pdf_filename = new_num + " - " + pdf_filename[len(old_num + " - "):]
                            elif " - " in pdf_filename:
                                prefix, rest = pdf_filename.split(" - ", 1)
                                if prefix != new_num:
                                    pdf_filename = new_num + " - " + rest
                            else:
                                pdf_filename = new_num + " - " + pdf_filename

                        new_key = TrackerBadPartKey(
                            job_folder_name=new_name,
                            pdf_filename=pdf_filename,
                            page=key.page,
                            file_fingerprint=key.file_fingerprint,
                            part_number=key.part_number,
                        )
                        updated_set.add(new_key.to_token())
                    else:
                        updated_set.add(token)
                setattr(self.state, set_name, updated_set)
            self._save_state()

    def get_bad_parts_snapshot(self, include_resolved: bool = False) -> Dict[str, List[BadPartDetailRecord]]:
        with self._lock:
            active_events = self._collect_active_events()
            active_tokens = set(active_events.keys())
            acknowledged_tokens = self.state.acknowledged_keys & active_tokens

            unack_records: List[BadPartDetailRecord] = []
            ack_records: List[BadPartDetailRecord] = []
            for token in sorted(active_tokens):
                event = active_events.get(token)
                if event is None:
                    continue
                record = self._build_detail_record(
                    key=event.key,
                    detected_at=event.detected_at,
                    is_acknowledged=token in acknowledged_tokens,
                )
                if record.is_acknowledged:
                    ack_records.append(record)
                else:
                    unack_records.append(record)

            payload: Dict[str, List[BadPartDetailRecord]] = {
                "unacknowledged": unack_records,
                "acknowledged": ack_records,
            }

            if include_resolved:
                resolved_records: List[BadPartDetailRecord] = []
                resolved_tokens = self.state.seen_keys - active_tokens
                for token in sorted(resolved_tokens):
                    try:
                        key = TrackerBadPartKey.from_token(token)
                    except Exception:
                        continue
                    resolved_records.append(
                        self._build_detail_record(
                            key=key,
                            detected_at="",
                            is_acknowledged=False,
                        )
                    )
                payload["resolved"] = resolved_records

            return payload
