from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from .metadata_cache import refresh_single_job, update_all_jobs_cache
from .metadata_snapshot import prune_orphan_job_archives
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
                self._reasons[job] = reason
                return True
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
                self._global_reason = reason
                return True
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
        self.archive_retention_days = getattr(config, "metadata_snapshot_retention_days", 30)
        self.archive_max_snapshots_per_job = getattr(config, "metadata_snapshot_max_per_job", 3)
        self.archive_daypart_limit = getattr(config, "metadata_snapshot_daypart_limit", True)
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
            archive_retention_days=self.archive_retention_days,
            archive_max_snapshots_per_job=self.archive_max_snapshots_per_job,
            archive_daypart_limit=self.archive_daypart_limit,
            consolidate_trackers=False,
        )

    def refresh_all_now(self, reason: str) -> dict:
        summary = update_all_jobs_cache(
            self.root_dir,
            consolidate_trackers=False,
            archive=True,
            archive_root=self.archive_root,
            archive_retention_days=self.archive_retention_days,
            archive_max_snapshots_per_job=self.archive_max_snapshots_per_job,
            archive_daypart_limit=self.archive_daypart_limit,
            force_rebuild=True,
        )
        self._prune_orphan_archives()
        return summary

    def run_scheduled_sweep(self, *, consolidate_trackers: bool = True) -> dict:
        summary = update_all_jobs_cache(
            self.root_dir,
            consolidate_trackers=consolidate_trackers,
            archive=True,
            archive_root=self.archive_root,
            archive_retention_days=self.archive_retention_days,
            archive_max_snapshots_per_job=self.archive_max_snapshots_per_job,
            archive_daypart_limit=self.archive_daypart_limit,
        )
        self._prune_orphan_archives()
        return summary

    def _prune_orphan_archives(self) -> None:
        if self.archive_root is None:
            return
        try:
            prune_orphan_job_archives(self.root_dir, self.archive_root)
        except Exception as exc:
            main_logger.error("Failed pruning orphan metadata archives: %s", exc, exc_info=True)

    def stop(self) -> None:
        self.scheduler.cancel_all()
