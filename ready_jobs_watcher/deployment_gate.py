"""
Deployment and visibility gate management for Ready Jobs.

Per-job metadata is stored at:
  <job>/.metadata/deployment_gate.json
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from typing import Dict, List, Optional
from .refresh_signals import touch_cnc_refresh_signal

main_logger = logging.getLogger("main")

DEPLOYMENT_GATE_FILENAME = "deployment_gate.json"
MODE_FACE_FRAME = "FACE-FRAME"
MODE_FRAMELESS = "FRAMELESS"
MODE_BOTH = "BOTH"
MODE_UNKNOWN = "UNKNOWN"
MODE_VALUES = {MODE_FACE_FRAME, MODE_FRAMELESS, MODE_BOTH, MODE_UNKNOWN}
PENDING_AUTO_RELEASE_HOURS = 30


class DeploymentGateManager:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self._lock = threading.RLock()

    @staticmethod
    def _now_iso() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    @staticmethod
    def normalize_mode(value: Optional[str]) -> str:
        raw = str(value or "").strip().upper().replace("_", "-")
        if raw in {"FACE-FRAME", "FACEFRAME"}:
            return MODE_FACE_FRAME
        if raw in {"FRAMELESS", "FL"}:
            return MODE_FRAMELESS
        if raw in {"BOTH", "FF/FL", "FF+FL"}:
            return MODE_BOTH
        return MODE_UNKNOWN

    @staticmethod
    def _mode_source(value: Optional[str]) -> str:
        raw = str(value or "").strip().upper()
        if not raw:
            return "UNKNOWN"
        return raw

    @staticmethod
    def _atomic_write_json(path: str, payload: Dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        if os.path.exists(path):
            os.remove(path)
        os.rename(temp_path, path)

    @staticmethod
    def _default_state(job_folder_name: str, deployed: bool = True) -> Dict:
        now = DeploymentGateManager._now_iso()
        return {
            "schemaVersion": 1,
            "jobFolderName": job_folder_name,
            "deployed": bool(deployed),
            "parseReady": bool(deployed),
            "hiddenFromProduction": False,
            "selectedMode": MODE_UNKNOWN,
            "modeDetection": {
                "candidate": MODE_UNKNOWN,
                "source": "UNKNOWN",
                "detectedAt": now,
            },
            "timers": {
                "retryAt": None,
                "remindAt": None,
                "autoReleaseAt": None,
                "lastActionAt": None,
            },
            "createdAt": now,
            "updatedAt": now,
        }

    def _metadata_path_for_job(self, job_folder_name: str) -> str:
        return os.path.join(self.root_dir, job_folder_name, ".metadata", DEPLOYMENT_GATE_FILENAME)

    def _coerce_state(self, job_folder_name: str, raw: Dict) -> Dict:
        state = self._default_state(job_folder_name, deployed=True)
        if not isinstance(raw, dict):
            return state
        state["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
        state["jobFolderName"] = job_folder_name
        state["deployed"] = bool(raw.get("deployed", True))
        state["parseReady"] = bool(raw.get("parseReady", state["deployed"]))
        state["hiddenFromProduction"] = bool(raw.get("hiddenFromProduction", False))
        state["selectedMode"] = self.normalize_mode(raw.get("selectedMode"))

        mode_detection = raw.get("modeDetection") if isinstance(raw.get("modeDetection"), dict) else {}
        state["modeDetection"] = {
            "candidate": self.normalize_mode(mode_detection.get("candidate")),
            "source": self._mode_source(mode_detection.get("source")),
            "detectedAt": str(mode_detection.get("detectedAt", state["createdAt"]) or state["createdAt"]),
        }

        timers = raw.get("timers") if isinstance(raw.get("timers"), dict) else {}
        state["timers"] = {
            "retryAt": timers.get("retryAt"),
            "remindAt": timers.get("remindAt"),
            "autoReleaseAt": timers.get("autoReleaseAt"),
            "lastActionAt": timers.get("lastActionAt"),
        }

        state["createdAt"] = str(raw.get("createdAt", state["createdAt"]) or state["createdAt"])
        state["updatedAt"] = str(raw.get("updatedAt", state["updatedAt"]) or state["updatedAt"])
        return state

    def load_state(self, job_folder_name: str, *, create_if_missing: bool = False, default_deployed: bool = True) -> Dict:
        path = self._metadata_path_for_job(job_folder_name)
        with self._lock:
            if not os.path.exists(path):
                state = self._default_state(job_folder_name, deployed=default_deployed)
                if create_if_missing:
                    self._atomic_write_json(path, state)
                return state
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return self._coerce_state(job_folder_name, raw)
            except Exception as exc:
                main_logger.error("Failed reading deployment gate for %s: %s", job_folder_name, exc)
                state = self._default_state(job_folder_name, deployed=default_deployed)
                if create_if_missing:
                    self._atomic_write_json(path, state)
                return state

    def save_state(self, job_folder_name: str, state: Dict) -> Dict:
        with self._lock:
            coerced = self._coerce_state(job_folder_name, state)
            coerced["updatedAt"] = self._now_iso()
            metadata_path = self._metadata_path_for_job(job_folder_name)
            self._atomic_write_json(metadata_path, coerced)
            touch_cnc_refresh_signal(
                job_folder_path=os.path.dirname(os.path.dirname(metadata_path)),
                reason="deployment_gate_updated",
                source="deployment_gate",
            )
            return coerced

    @staticmethod
    def _auto_release_at_from(action_at: datetime.datetime) -> str:
        return (action_at + datetime.timedelta(hours=PENDING_AUTO_RELEASE_HOURS)).isoformat()

    def update_state(self, job_folder_name: str, operator_action: bool = False, **updates) -> Dict:
        with self._lock:
            state = self.load_state(job_folder_name, create_if_missing=True, default_deployed=True)
            mode_detection = updates.pop("modeDetection", None)
            timers = updates.pop("timers", None)

            state.update({k: v for k, v in updates.items() if k in {
                "deployed", "parseReady", "hiddenFromProduction", "selectedMode"
            }})

            if "selectedMode" in updates:
                state["selectedMode"] = self.normalize_mode(updates.get("selectedMode"))

            if isinstance(mode_detection, dict):
                current = state.get("modeDetection", {})
                current["candidate"] = self.normalize_mode(mode_detection.get("candidate", current.get("candidate")))
                current["source"] = self._mode_source(mode_detection.get("source", current.get("source")))
                current["detectedAt"] = str(mode_detection.get("detectedAt") or self._now_iso())
                state["modeDetection"] = current

            if isinstance(timers, dict):
                current_timers = state.get("timers", {})
                for key in ("retryAt", "remindAt", "autoReleaseAt", "lastActionAt"):
                    if key in timers:
                        current_timers[key] = timers[key]
                state["timers"] = current_timers

            if operator_action and not bool(state.get("deployed", True)):
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                current_timers = state.get("timers", {})
                current_timers["lastActionAt"] = now_dt.isoformat()
                current_timers["autoReleaseAt"] = self._auto_release_at_from(now_dt)
                state["timers"] = current_timers

            return self.save_state(job_folder_name, state)

    def ensure_pending_for_new_job(self, job_folder_name: str, *, detected_mode: str = MODE_UNKNOWN, detection_source: str = "UNKNOWN") -> Dict:
        metadata_path = self._metadata_path_for_job(job_folder_name)
        had_existing_state = os.path.exists(metadata_path)
        state = self.load_state(job_folder_name, create_if_missing=True, default_deployed=False)
        was_pending = not bool(state.get("deployed", True))
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        current_timers = state.get("timers", {})

        state["deployed"] = False
        state["parseReady"] = False
        state["selectedMode"] = self.normalize_mode(state.get("selectedMode") or MODE_UNKNOWN)
        state["modeDetection"] = {
            "candidate": self.normalize_mode(detected_mode),
            "source": self._mode_source(detection_source),
            "detectedAt": self._now_iso(),
        }

        if not had_existing_state or not was_pending or not current_timers.get("autoReleaseAt"):
            current_timers["autoReleaseAt"] = self._auto_release_at_from(now_dt)
        if not had_existing_state or not was_pending or not current_timers.get("lastActionAt"):
            current_timers["lastActionAt"] = now_dt.isoformat()
        state["timers"] = current_timers
        return self.save_state(job_folder_name, state)

    def mark_deployed(self, job_folder_name: str, selected_mode: Optional[str] = None) -> Dict:
        updates = {
            "deployed": True,
            "parseReady": False,
            "hiddenFromProduction": False,
        }
        if selected_mode is not None:
            updates["selectedMode"] = self.normalize_mode(selected_mode)
        return self.update_state(job_folder_name, **updates)

    def mark_parse_ready(self, job_folder_name: str, parse_ready: bool) -> Dict:
        return self.update_state(job_folder_name, parseReady=bool(parse_ready))

    def set_selected_mode(
        self,
        job_folder_name: str,
        selected_mode: Optional[str],
        *,
        mark_as_operator_action: bool = True,
    ) -> Dict:
        return self.update_state(
            job_folder_name,
            selectedMode=self.normalize_mode(selected_mode),
            operator_action=mark_as_operator_action,
        )

    def set_mode_detection(
        self,
        job_folder_name: str,
        candidate: Optional[str],
        source: str,
        *,
        mark_as_operator_action: bool = True,
    ) -> Dict:
        return self.update_state(
            job_folder_name,
            modeDetection={
                "candidate": self.normalize_mode(candidate),
                "source": source,
                "detectedAt": self._now_iso(),
            },
            operator_action=mark_as_operator_action,
        )

    def schedule_reminder(
        self,
        job_folder_name: str,
        minutes: int = 15,
        *,
        mark_as_operator_action: bool = True,
    ) -> Dict:
        remind_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()
        return self.update_state(
            job_folder_name,
            timers={"remindAt": remind_at},
            operator_action=mark_as_operator_action,
        )

    def clear_timers(self, job_folder_name: str) -> Dict:
        return self.update_state(
            job_folder_name,
            timers={
                "retryAt": None,
                "remindAt": None,
                "autoReleaseAt": None,
                "lastActionAt": None,
            },
        )

    def mark_operator_action(self, job_folder_name: str) -> Dict:
        return self.update_state(job_folder_name, operator_action=True)

    def should_process_job_folder(self, job_folder_path: str) -> bool:
        job_folder_name = os.path.basename(os.path.normpath(job_folder_path))
        state = self.load_state(job_folder_name, create_if_missing=False, default_deployed=True)
        return bool(state.get("deployed", True))

    def get_visibility(self, job_folder_name: str, is_debug_build: bool) -> bool:
        state = self.load_state(job_folder_name, create_if_missing=False, default_deployed=True)
        if not bool(state.get("deployed", True)):
            return False
        if not bool(state.get("parseReady", True)):
            return False
        if bool(state.get("hiddenFromProduction", False)) and not is_debug_build:
            return False
        return True

    def list_job_states(self) -> List[Dict]:
        rows: List[Dict] = []
        if not os.path.isdir(self.root_dir):
            return rows
        try:
            with os.scandir(self.root_dir) as entries:
                for entry in entries:
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    state = self.load_state(entry.name, create_if_missing=False, default_deployed=True)
                    rows.append(state)
        except OSError as exc:
            main_logger.error("Failed listing deployment gate jobs: %s", exc)
        rows.sort(key=lambda item: str(item.get("jobFolderName", "")).lower())
        return rows


def load_job_gate_state(root_dir: str, job_folder_name: str) -> Dict:
    return DeploymentGateManager(root_dir).load_state(job_folder_name, create_if_missing=False, default_deployed=True)


def derive_state(state: Dict) -> str:
    """
    Derive a single presentation state from raw gate booleans.

    PENDING  -> not deployed (awaiting operator release)
    PARSING  -> deployed but parse not yet complete
    ACTIVE   -> deployed and parse complete (visible to production)

    Defaults match load_state defaults (deployed=True, parseReady=True) so a
    legacy gate with missing keys reads as ACTIVE.
    """
    if not bool(state.get("deployed", True)):
        return "PENDING"
    if not bool(state.get("parseReady", True)):
        return "PARSING"
    return "ACTIVE"


def should_process_job_folder(root_dir: str, job_folder_path: str) -> bool:
    return DeploymentGateManager(root_dir).should_process_job_folder(job_folder_path)


def ensure_hidden_gate_for_folder(root_dir: str, folder_name: str) -> bool:
    """
    Create a hidden deployment gate for a folder if one does not already exist.
    Returns True if a gate was created, False if one already existed.
    Skips dot-hidden OS folders (e.g. .metadata, .git).
    """
    if folder_name.startswith("."):
        return False
    manager = DeploymentGateManager(root_dir)
    gate_path = manager._metadata_path_for_job(folder_name)
    if os.path.exists(gate_path):
        return False
    now = DeploymentGateManager._now_iso()
    state = {
        "schemaVersion": 1,
        "jobFolderName": folder_name,
        "deployed": False,
        "parseReady": False,
        "hiddenFromProduction": False,
        "selectedMode": MODE_UNKNOWN,
        "modeDetection": {
            "candidate": MODE_UNKNOWN,
            "source": "AUTO_HIDDEN",
            "detectedAt": now,
        },
        "timers": {
            "retryAt": None,
            "remindAt": None,
            "autoReleaseAt": None,
            "lastActionAt": None,
        },
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        DeploymentGateManager._atomic_write_json(gate_path, state)
        main_logger.info("Created hidden gate for unrecognized folder: %s", folder_name)
        return True
    except Exception as exc:
        main_logger.warning("Failed to create hidden gate for %s: %s", folder_name, exc)
        return False


def ensure_hidden_gates_for_all_folders(root_dir: str) -> int:
    """
    Scan root_dir and stamp a hidden deployment_gate.json on every subfolder
    that does not already have one. Skips dot-hidden OS folders.
    Returns the count of gates created.
    """
    if not os.path.isdir(root_dir):
        return 0
    created = 0
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue
                if ensure_hidden_gate_for_folder(root_dir, entry.name):
                    created += 1
    except OSError as exc:
        main_logger.error("Failed scanning root dir for gate bootstrap: %s", exc)
    if created:
        main_logger.info("Bootstrapped hidden gates for %d ungated folders.", created)
    return created
