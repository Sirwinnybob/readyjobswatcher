"""
Local alert escalation for tracker bad-parts.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .config import Config
from .notifications import send_notification
from .tracker_bad_parts import (
    BadPartDetailRecord,
    TrackerBadPartEvent,
    TrackerBadPartKey,
    TrackerBadPartsMonitor,
)

badparts_logger = logging.getLogger("badparts")


@dataclass
class AlertBatch:
    events: List[TrackerBadPartEvent]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def keys(self) -> List[TrackerBadPartKey]:
        return [event.key for event in self.events]

    def build_toast_message(self, max_rows: int = 6) -> str:
        if not self.events:
            return "No bad-part events."
        rows = []
        for event in self.events[:max_rows]:
            rows.append(
                f"{event.key.job_folder_name} | {event.material_or_pdf} | Pg {event.key.page} | Part {event.key.part_number}"
            )
        if len(self.events) > max_rows:
            rows.append(f"...and {len(self.events) - max_rows} more")
        return "\n".join(rows)


class AlertCoordinator:
    """
    Thread-safe queue + dispatch worker for bad-parts alerts.
    """

    def __init__(
        self,
        config: Config,
        tracker_monitor: TrackerBadPartsMonitor,
        popup_notifier: Optional[Callable[[AlertBatch], None]] = None,
    ):
        self.config = config
        self.tracker_monitor = tracker_monitor
        self.popup_notifier = popup_notifier
        self._queue: "queue.Queue[AlertBatch]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="RJW-AlertCoordinator")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def submit_events(self, events: List[TrackerBadPartEvent]) -> None:
        if not events:
            return
        batch = AlertBatch(events=events)
        self._queue.put(batch)

    def acknowledge_batch(self, batch: AlertBatch) -> int:
        count = self.tracker_monitor.acknowledge_keys(batch.keys)
        badparts_logger.info("TRACKER_ACK batch_count=%s", count)
        return count

    def acknowledge_keys(self, keys: List[TrackerBadPartKey]) -> int:
        count = self.tracker_monitor.acknowledge_keys(keys)
        badparts_logger.info("TRACKER_ACK selected_count=%s", count)
        return count

    def unacknowledge_keys(self, keys: List[TrackerBadPartKey]) -> int:
        count = self.tracker_monitor.unacknowledge_keys(keys)
        badparts_logger.info("TRACKER_UNACK selected_count=%s", count)
        return count

    def get_bad_parts_snapshot(self, include_resolved: bool = False):
        return self.tracker_monitor.get_bad_parts_snapshot(include_resolved=include_resolved)

    def build_detail_records_for_events(self, events: List[TrackerBadPartEvent]) -> List[BadPartDetailRecord]:
        ack_tokens = set(self.tracker_monitor.state.acknowledged_keys)
        records: List[BadPartDetailRecord] = []
        for event in events:
            token = event.key.to_token()
            records.append(
                self.tracker_monitor.get_detail_record(
                    key=event.key,
                    detected_at=event.detected_at,
                    is_acknowledged=token in ack_tokens,
                )
            )
        return records

    def _play_sound(self) -> None:
        profile = str(self.config.bad_parts_sound_profile or "").strip().lower()
        if profile != "triple_beep":
            return

        try:
            import winsound

            for _ in range(3):
                winsound.Beep(1300, 180)
                time.sleep(0.08)
        except Exception as exc:
            badparts_logger.error(f"Failed alert sound playback: {exc}")

    def _dispatch(self, batch: AlertBatch) -> None:
        if self.config.bad_parts_popup_enabled and self.popup_notifier:
            try:
                self.popup_notifier(batch)
            except Exception as exc:
                badparts_logger.error(f"Failed popup notification dispatch: {exc}")

        if self.config.bad_parts_toast_enabled:
            send_notification(
                title=f"Bad Part Alert ({len(batch.events)})",
                message=batch.build_toast_message(),
                duration="long",
            )

        self._play_sound()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                batch = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._dispatch(batch)
            except Exception as exc:
                badparts_logger.error(f"Alert dispatch failed: {exc}", exc_info=True)

    def test_alert(self) -> None:
        test_event = TrackerBadPartEvent(
            key=TrackerBadPartKey(
                job_folder_name="TEST JOB",
                pdf_filename="Test Material.pdf",
                page=1,
                file_fingerprint="__test__",
                part_number=101,
            ),
            material_or_pdf="Test Material",
            detected_at=datetime.now(timezone.utc).isoformat(),
        )
        self.submit_events([test_event])
