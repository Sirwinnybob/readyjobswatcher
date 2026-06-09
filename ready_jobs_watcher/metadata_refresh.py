from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from .metadata_cache import refresh_single_job, update_all_jobs_cache
from .metadata_inventory import find_job_folder_for_path, is_rebuild_trigger

main_logger = logging.getLogger("main")


class DebouncedMetadataRefreshScheduler:
    def __init__(
        self,
        *,
        root_dir: os.PathLike | str,
        refresh_callback: Callable[[Path, str], None],
        refresh_all_callback: Optional[Callable[[str], None]] = None,
        delay_seconds: float = 8.0,
        timer_factory=threading.Timer,
    ):
        self.root_dir = Path(root_dir)
        self.refresh_callback = refresh_callback
        self.refresh_all_callback = refresh_all_callback
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.timer_factory = timer_factory
        self._lock = threading.RLock()
        self._timers: dict[Path, object] = {}
        self._reasons: dict[Path, str] = {}
        self._global_timer: Optional[object] = None
        self._global_reason: Optional[str] = None

    def schedule(self, job_folder: os.PathLike | str, reason: str) -> bool:
        job = Path(job_folder)
        if not job.name:
            return False

        def _run():
            with self._lock:
                self._timers.pop(job, None)
                latest_reason = self._reasons.pop(job, reason)
            try:
                self.refresh_callback(job, latest_reason)
            except Exception as exc:
                main_logger.error("Debounced metadata refresh failed for %s: %s", job, exc, exc_info=True)

        with self._lock:
            existing = self._timers.get(job)
            if existing is not None:
                existing.cancel()
            self._reasons[job] = reason
            timer = self.timer_factory(self.delay_seconds, _run)
            timer.daemon = True
            timer.name = f"MetadataRefresh-{job.name}"
            self._timers[job] = timer
            timer.start()
        return True

    def schedule_all(self, reason: str) -> bool:
        if self.refresh_all_callback is None:
            return False

        def _run():
            with self._lock:
                self._global_timer = None
                latest_reason = self._global_reason or reason
                self._global_reason = None
            try:
                self.refresh_all_callback(latest_reason)
            except Exception as exc:
                main_logger.error("Debounced global metadata refresh failed: %s", exc, exc_info=True)

        with self._lock:
            if self._global_timer is not None:
                self._global_timer.cancel()
            self._global_reason = reason
            timer = self.timer_factory(self.delay_seconds, _run)
            timer.daemon = True
            timer.name = "MetadataRefresh-AllJobs"
            self._global_timer = timer
            timer.start()
        return True

    def schedule_for_changed_path(self, path: os.PathLike | str, reason: str) -> bool:
        changed_path = Path(path)
        if not is_rebuild_trigger(changed_path, self.root_dir):
            return False
        try:
            rel = changed_path.resolve().relative_to(self.root_dir.resolve())
        except (OSError, ValueError):
            rel = None
        if rel is not None and rel.parts:
            first_part = str(rel.parts[0]).lower()
            if first_part in {"production_order.json", ".metadata"}:
                return self.schedule_all(reason)
        job_folder = find_job_folder_for_path(changed_path, self.root_dir)
        if job_folder is None:
            return False
        return self.schedule(job_folder, reason)

    def cancel_all(self) -> None:
        with self._lock:
            timers = list(self._timers.values())
            global_timer = self._global_timer
            self._timers.clear()
            self._reasons.clear()
            self._global_timer = None
            self._global_reason = None
        for timer in timers:
            timer.cancel()
        if global_timer is not None:
            global_timer.cancel()


class MetadataRefreshService:
    def __init__(self, config):
        self.config = config
        self.root_dir = Path(config.ROOT_DIR)
        self.archive_root = (
            Path(getattr(config, "metadata_snapshot_archive_dir", "metadata_snapshots"))
            if getattr(config, "metadata_snapshot_enabled", True)
            else None
        )
        self.scheduler = DebouncedMetadataRefreshScheduler(
            root_dir=self.root_dir,
            refresh_callback=self.refresh_job_now,
            refresh_all_callback=self.refresh_all_now,
            delay_seconds=getattr(config, "metadata_cache_debounce_seconds", 8),
        )

    def schedule_path(self, path: os.PathLike | str, reason: str) -> bool:
        return self.scheduler.schedule_for_changed_path(path, reason)

    def schedule_job(self, job_folder: os.PathLike | str, reason: str) -> bool:
        return self.scheduler.schedule(job_folder, reason)

    def refresh_job_now(self, job_folder: Path, reason: str) -> None:
        refresh_single_job(
            self.root_dir,
            Path(job_folder),
            reason=reason,
            archive_root=self.archive_root,
            consolidate_trackers=False,
        )

    def refresh_all_now(self, reason: str) -> dict:
        return update_all_jobs_cache(
            self.root_dir,
            consolidate_trackers=False,
            archive=True,
            archive_root=self.archive_root,
            force_rebuild=True,
        )

    def run_scheduled_sweep(self, *, consolidate_trackers: bool = True) -> dict:
        return update_all_jobs_cache(
            self.root_dir,
            consolidate_trackers=consolidate_trackers,
            archive=True,
            archive_root=self.archive_root,
        )

    def stop(self) -> None:
        self.scheduler.cancel_all()
