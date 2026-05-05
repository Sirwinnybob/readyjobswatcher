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
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .config import BASE_DATA_DIR, Config
from .file_handler import JobProcessor

badparts_logger = logging.getLogger("badparts")

TRACKER_STATE_FILE = os.path.join(BASE_DATA_DIR, "tracker_bad_parts_state.json")


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

    def __init__(self, config: Config, state_file: str = TRACKER_STATE_FILE):
        self.config = config
        self.state_file = state_file
        self._lock = threading.Lock()
        self._material_cache: Dict[Tuple[str, str], str] = {}
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

        meta_name = os.path.splitext(key.pdf_filename)[0] + ".json"
        meta_path = os.path.join(
            self.config.ROOT_DIR,
            key.job_folder_name,
            self.config.CNC_SUBDIR,
            ".metadata",
            meta_name,
        )
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

    def _collect_active_events(self) -> Dict[str, TrackerBadPartEvent]:
        action_rows: List[Tuple[str, str, int, Dict[str, object]]] = []
        for job_folder_name, tracker_file_path in self._iter_tracker_files():
            try:
                with open(tracker_file_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if not isinstance(payload, dict):
                    continue
                actions = payload.get("actions", [])
                if not isinstance(actions, list):
                    continue
                for idx, action in enumerate(actions):
                    if isinstance(action, dict):
                        timestamp = str(action.get("timestamp", "") or "")
                        action_rows.append((timestamp, tracker_file_path, idx, {"jobFolderName": job_folder_name, **action}))
            except Exception as exc:
                badparts_logger.warning(f"Skipping malformed tracker file: {tracker_file_path} ({exc})")

        action_rows.sort(key=lambda row: (row[0], row[1], row[2]))

        status_map: Dict[str, bool] = {}
        event_map: Dict[str, TrackerBadPartEvent] = {}
        for _, _, _, action in action_rows:
            action_name = str(action.get("action", "") or "").strip().lower()
            if action_name not in ("bad_part", "unbad_part"):
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
                status_map[token] = True
                detected_at = str(action.get("timestamp", "") or "")
                event_map[token] = TrackerBadPartEvent(
                    key=key,
                    material_or_pdf=self._load_material_name(key),
                    detected_at=detected_at,
                )
            else:
                status_map[token] = False

        active_events: Dict[str, TrackerBadPartEvent] = {}
        for token, is_active in status_map.items():
            if not is_active:
                continue
            event = event_map.get(token)
            if event is not None:
                active_events[token] = event
        return active_events

    def scan_once(self) -> List[TrackerBadPartEvent]:
        """
        Rebuild active tracker bad-parts and return newly activated events.
        """
        with self._lock:
            active_events = self._collect_active_events()
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

