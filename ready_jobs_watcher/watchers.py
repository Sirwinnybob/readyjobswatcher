"""
File System Watchers Module.

Implements `watchdog` event handlers to actively monitor configured directories
for file creations, modifications, deletions, and moves. Triggers automated
renaming, PDF scanning, and log file processing.
"""
import logging
import time
import threading
import os
from typing import Optional
from watchdog.events import FileSystemEventHandler

# Legacy desktop bad-parts log path (read-only in watcher).
from .bad_parts_checker import BAD_PART_LOG_FILE
from .file_handler import should_ignore_folder, should_ignore_file
from .utils import is_hidden, ALLOWED_SHEETS_PATTERN
from .tracker_bad_parts import TrackerBadPartsMonitor
from .alert_coordinator import AlertCoordinator
from .cabinet_sheet_indexer import build_reference_index_for_pdf_event
from .hardwoods_cutlist_indexer import build_hardwoods_cutlist_index_for_pdf_event
from .dae_converter import convert_dae_to_medium_glb
from .remake_candidates_indexer import (
    refresh_unresolved_bad_parts_for_job,
    refresh_unresolved_bad_parts_all,
    derive_job_from_tracker_path,
)
from .sync_conflict_resolver import is_sync_conflict_path, resolve_sync_conflict_file


main_logger = logging.getLogger('main')

# Thread-safe lock for log file processing
IS_PROCESSING_LOG_FILE_LOCK = threading.Lock()


def _path_parts_lower(path: str) -> list[str]:
    normalized = str(path or "").replace("/", "\\")
    return [part.lower() for part in normalized.split("\\") if part]


def _is_internal_watcher_path(path: str) -> bool:
    """
    Return True for watcher-owned bookkeeping paths that should not drive rename logic.

    The PDF/tracker handler still sees tracker signal files; this only keeps the
    rename handler from logging and processing its own generated metadata writes.
    """
    if not path:
        return True
    parts = _path_parts_lower(path)
    if ".metadata" in parts or ".tracker" in parts:
        return True
    return should_ignore_file(os.path.basename(path))


class RenameHandler(FileSystemEventHandler):
    """
    Event handler for file and directory creation or movement events.

    Detects new job folders and files, scheduling them for delayed renaming
    and processing to ensure template files are not renamed prematurely.
    """
    def __init__(self, config, job_processor, app_state, pending_queue=None, executor=None, deployment_gate=None):
        """
        Initialize the RenameHandler.

        Args:
            config (Config): Application configuration.
            job_processor (JobProcessor): Processor instance for handling logic.
            app_state (Application): Core application state.
            pending_queue (PendingQueue, optional): Queue for persisting scheduled tasks.
            executor (ThreadPoolExecutor, optional): Executor for background task offloading.
        """
        super().__init__()
        self.config = config
        self.job_processor = job_processor
        self.app_state = app_state
        self.pending_queue = pending_queue
        self.executor = executor  # ThreadPoolExecutor for background tasks
        self._pending_folders = {}  # Track folders waiting to be processed: {folder_path: scheduled_time}
        self._pending_folders_lock = threading.Lock()  # Lock for thread-safe access
        self._folder_delay_seconds = config.new_folder_delay_seconds
        self.deployment_gate = deployment_gate

    def _is_top_level_job_folder(self, folder_path: str) -> bool:
        root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
        parent_norm = os.path.normcase(os.path.normpath(os.path.dirname(folder_path)))
        is_top_level = parent_norm == root_norm
        is_job_folder = self.job_processor.is_job_folder(folder_path)
        return is_top_level and is_job_folder

    def _schedule_folder_processing(self, folder_path: str, delay_seconds: Optional[float] = None, persist_in_queue: bool = True):
        """
        Schedule a folder to be processed after a configured delay.

        Args:
            folder_path (str): Full path to the detected folder.
        """
        delay = self._folder_delay_seconds if delay_seconds is None else max(0.0, float(delay_seconds))
        scheduled_time = time.time() + delay

        with self._pending_folders_lock:
            self._pending_folders[folder_path] = scheduled_time

        main_logger.info(f"Scheduled folder processing in {delay}s: {folder_path}")

        # Save to persistent queue
        if self.pending_queue and persist_in_queue:
            self.pending_queue.add_pending_folder(folder_path, scheduled_time)

        # Actual task to process the folder
        def _process_task():
            try:
                current_time = time.time()
                should_process = False
                with self._pending_folders_lock:
                    run_at = self._pending_folders.get(folder_path)
                    if run_at is None:
                        main_logger.debug(f"Folder processing was cancelled: {folder_path}")
                    elif run_at > current_time + 0.5:
                        main_logger.debug(f"Skipping outdated folder timer run for {folder_path}; newer schedule exists.")
                    else:
                        main_logger.info(f"Processing delayed folder: {folder_path}")
                        should_process = True

                if should_process:
                    processed_ok = bool(self.job_processor.process_job_folder(folder_path))
                    if processed_ok:
                        with self._pending_folders_lock:
                            self._pending_folders.pop(folder_path, None)
                        if self.pending_queue:
                            self.pending_queue.remove_pending_folder(folder_path)
                    else:
                        retry_delay = max(30.0, float(self._folder_delay_seconds))
                        next_run_at = time.time() + retry_delay
                        with self._pending_folders_lock:
                            self._pending_folders[folder_path] = next_run_at
                        if self.pending_queue:
                            self.pending_queue.add_pending_folder(folder_path, next_run_at)
                        self._schedule_folder_processing(folder_path, delay_seconds=retry_delay, persist_in_queue=False)
                        main_logger.warning(
                            "Delayed folder processing deferred after transient failure; retry in %ss: %s",
                            retry_delay,
                            folder_path,
                        )
            except Exception as e:
                main_logger.error(f"Error in delayed folder processing for {folder_path}: {e}", exc_info=True)

        def _timer_callback():
            # Submit actual processing to executor or run in thread
            if self.executor:
                self.executor.submit(_process_task)
            else:
                thread = threading.Thread(target=_process_task, daemon=True, name=f"DelayedFolderProcess-{os.path.basename(folder_path)}")
                thread.start()

        # Use a timer to wait outside of the thread pool
        timer = threading.Timer(delay, _timer_callback)
        timer.name = f"Timer-FolderProcess-{os.path.basename(folder_path)}"
        timer.daemon = True
        timer.start()

    def on_created(self, event):
        """
        Triggered when a file or directory is created.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and is_sync_conflict_path(event.src_path):
                resolve_sync_conflict_file(event.src_path, self.config.ROOT_DIR)
                return

            if _is_internal_watcher_path(event.src_path):
                return

            main_logger.debug(f"on_created triggered for {event.src_path}")

            # Skip hidden files/folders
            if is_hidden(event.src_path):
                main_logger.debug(f"Skipping hidden item: {event.src_path}")
                return

            if event.is_directory:
                folder_name = os.path.basename(event.src_path)

                root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
                parent_norm = os.path.normcase(os.path.normpath(os.path.dirname(event.src_path)))
                is_top_level = parent_norm == root_norm

                # For every new top-level folder (including ignored ones), immediately
                # stamp a hidden deployment gate so tablets never see ungated folders.
                if is_top_level and not is_hidden(event.src_path):
                    from .deployment_gate import ensure_hidden_gate_for_folder
                    ensure_hidden_gate_for_folder(self.config.ROOT_DIR, folder_name)

                # Skip template folders (Face Frame, Frameless, New Folder, etc.)
                if should_ignore_folder(folder_name):
                    main_logger.debug(f"Skipping template folder: {event.src_path}")
                    return

                is_job_folder = self.job_processor.is_job_folder(event.src_path)
                if not is_top_level or not is_job_folder:
                    main_logger.debug(
                        "Ignoring non-job directory create event: path=%s topLevel=%s jobFolder=%s",
                        event.src_path,
                        is_top_level,
                        is_job_folder,
                    )
                    return

                # New top-level job folder created - schedule processing after delay
                main_logger.info(f"New job folder created: {event.src_path}")
                if hasattr(self.app_state, "on_new_job_folder_detected"):
                    self.app_state.on_new_job_folder_detected(event.src_path)
                self._schedule_folder_processing(event.src_path)
            else:
                file_name = os.path.basename(event.src_path)

                # Skip ignored files (Thumbs.db, temp files, etc.)
                if should_ignore_file(file_name):
                    main_logger.debug(f"Skipping ignored file: {event.src_path}")
                    return

                # New file created in existing job folder - process just this file
                parent_folder = os.path.dirname(event.src_path)
                parent_base_name = os.path.basename(parent_folder)
                job_num = self.job_processor.extract_job_number(parent_base_name)

                if job_num:
                    main_logger.info(f"File created in job folder: {event.src_path}")
                    self.job_processor.process_file(event.src_path, job_num, parent_folder)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_created for {event.src_path}: {e}")

    def on_modified(self, event):
        """
        Triggered when a file or directory is modified.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if _is_internal_watcher_path(event.src_path):
                return
            main_logger.debug(f"on_modified triggered for {event.src_path}")
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_modified for {event.src_path}: {e}")

    def on_moved(self, event):
        """
        Triggered when a file or directory is moved or renamed.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and is_sync_conflict_path(event.dest_path):
                resolve_sync_conflict_file(event.dest_path, self.config.ROOT_DIR)
                return

            if _is_internal_watcher_path(event.src_path) or _is_internal_watcher_path(event.dest_path):
                return

            main_logger.debug(f"on_moved triggered for {event.src_path} -> {event.dest_path}")

            # Skip hidden destinations
            if is_hidden(event.dest_path):
                main_logger.debug(f"Skipping hidden destination: {event.dest_path}")
                return

            main_logger.info(f"Moved/renamed event detected: {event.src_path} -> {event.dest_path} (is_directory={event.is_directory})")
            if event.is_directory:
                old_folder_name = os.path.basename(event.src_path)
                folder_name = os.path.basename(event.dest_path)

                # Skip template folders
                if should_ignore_folder(folder_name):
                    main_logger.debug(f"Skipping template folder: {event.dest_path}")
                    return

                # Check if it is a top-level directory rename
                root_norm = os.path.normcase(os.path.normpath(self.config.ROOT_DIR))
                src_parent = os.path.normcase(os.path.normpath(os.path.dirname(event.src_path)))
                dest_parent = os.path.normcase(os.path.normpath(os.path.dirname(event.dest_path)))

                is_top_level_rename = (
                    src_parent == root_norm
                    and dest_parent == root_norm
                    and old_folder_name != folder_name
                    and not old_folder_name.startswith(".")
                    and not folder_name.startswith(".")
                )

                if is_top_level_rename:
                    main_logger.info(f"Top-level job folder renamed: {event.src_path} -> {event.dest_path}")
                    if hasattr(self.app_state, "rename_job"):
                        self.app_state.rename_job(old_folder_name, folder_name)

                    if self.deployment_gate is not None:
                        state = self.deployment_gate.load_state(folder_name)
                        is_deployed = bool(state.get("deployed", False))
                    else:
                        is_deployed = False

                    if not is_deployed:
                        if hasattr(self.app_state, "on_new_job_folder_detected"):
                            self.app_state.on_new_job_folder_detected(event.dest_path)

                    # Run an immediate pass so files in the renamed folder are prefixed right away.
                    try:
                        self.job_processor.process_job_folder(event.dest_path)
                    except Exception as exc:
                        main_logger.warning(
                            "Immediate folder processing after rename failed; delayed retry will handle it: %s (%s)",
                            event.dest_path,
                            exc,
                        )
                    self._schedule_folder_processing(event.dest_path)
                    return

                if self._is_top_level_job_folder(event.dest_path):
                    main_logger.info(f"Top-level job folder moved/renamed: {event.dest_path}")
                    if hasattr(self.app_state, "on_new_job_folder_detected"):
                        self.app_state.on_new_job_folder_detected(event.dest_path)
                    # Run an immediate pass so files in the renamed folder are prefixed right away.
                    try:
                        self.job_processor.process_job_folder(event.dest_path)
                    except Exception as exc:
                        main_logger.warning(
                            "Immediate folder processing after rename failed; delayed retry will handle it: %s (%s)",
                            event.dest_path,
                            exc,
                        )
                    self._schedule_folder_processing(event.dest_path)
                    return

                self.job_processor.process_job_folder(event.dest_path, include_cnc=True)
            else:
                file_name = os.path.basename(event.dest_path)
                if should_ignore_file(file_name):
                    main_logger.debug(f"Skipping ignored moved file: {event.dest_path}")
                    return
                parent_folder = os.path.dirname(event.dest_path)
                parent_base_name = os.path.basename(parent_folder)
                job_num = self.job_processor.extract_job_number(parent_base_name)
                if job_num:
                    self.job_processor.process_file(event.dest_path, job_num, parent_folder)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_moved for {event.src_path} -> {event.dest_path}: {e}")

class PdfChangeHandler(FileSystemEventHandler):
    """
    Handles recursive modifications to files for tracker bad-part checks and dark mode conversion.
    """
    def __init__(
        self,
        config,
        rename_handler=None,
        pending_queue=None,
        executor=None,
        tracker_monitor: Optional[TrackerBadPartsMonitor] = None,
        alert_coordinator: Optional[AlertCoordinator] = None,
        deployment_gate=None,
        metadata_refresh_service=None,
    ):
        """
        Initialize the PdfChangeHandler.

        Args:
            config (Config): Application configuration.
            rename_handler (RenameHandler, optional): Reference to check if folders are pending.
            pending_queue (PendingQueue, optional): Queue for persisting scheduled conversions.
            executor (ThreadPoolExecutor, optional): Executor for background task offloading.
        """
        super().__init__()
        self.config = config
        self.rename_handler = rename_handler  # Reference to check pending folders
        self.pending_queue = pending_queue
        self.executor = executor  # ThreadPoolExecutor for background tasks
        self._conversion_cooldown = {}  # Track last conversion time per file
        self._cooldown_lock = threading.Lock()  # Lock for thread-safe access to cooldown dict
        self._cooldown_seconds = config.pdf_conversion_delay_seconds
        self._conversion_count = 0  # Track number of conversions for periodic cleanup
        self._count_lock = threading.Lock()  # Lock for thread-safe counter increment
        self.tracker_monitor = tracker_monitor
        self.alert_coordinator = alert_coordinator
        self._tracker_scan_timer = None
        self._tracker_scan_lock = threading.Lock()
        self._index_reparse_delay_seconds = 10.0
        self._index_reparse_timers = {}
        self._index_reparse_lock = threading.Lock()
        self._dae_reparse_timers = {}
        self._dae_reparse_lock = threading.Lock()
        self.deployment_gate = deployment_gate
        self.metadata_refresh_service = metadata_refresh_service

    def _is_root_available(self) -> bool:
        try:
            return os.path.isdir(self.config.ROOT_DIR)
        except OSError:
            return False

    def _schedule_metadata_refresh(self, src_path: str, reason: str):
        if self.metadata_refresh_service is None:
            return
        try:
            scheduled = self.metadata_refresh_service.schedule_path(src_path, reason)
            if scheduled:
                main_logger.debug("Scheduled metadata cache refresh (%s): %s", reason, src_path)
        except Exception as exc:
            main_logger.error("Failed scheduling metadata cache refresh for %s: %s", src_path, exc, exc_info=True)

    def _reschedule_pending_pdf_conversion(self, pdf_path: str, invert_images: bool, delay_seconds: float = 60.0):
        retry_delay = max(5.0, float(delay_seconds))
        retry_at = time.time() + retry_delay
        if self.pending_queue:
            self.pending_queue.add_pending_pdf(pdf_path, retry_at, invert_images)
        self._schedule_pdf_conversion(pdf_path, invert_images, delay_seconds=retry_delay)
        main_logger.warning(
            "Deferred PDF conversion due to transient/offline condition; retry in %ss: %s",
            retry_delay,
            pdf_path,
        )

    @staticmethod
    def _resolve_job_folder_for_pdf(pdf_path: str) -> str:
        normalized_path = pdf_path.replace('/', '\\')
        folder = os.path.dirname(normalized_path)
        if os.path.basename(folder).upper() == "DARK MODE":
            return os.path.dirname(folder)
        return folder

    @staticmethod
    def _is_watcher_refresh_signal(file_path: str) -> bool:
        return os.path.basename(str(file_path or "").replace("/", "\\")).lower() == "watcher_refresh_watcher.json"

    @staticmethod
    def _is_tracker_stream_file(file_path: str) -> bool:
        normalized = file_path.replace('/', '\\').lower()
        if PdfChangeHandler._is_watcher_refresh_signal(normalized):
            return False
        if "\\cnc\\.tracker\\events\\" in normalized and normalized.endswith(".ndjson"):
            return True
        return normalized.endswith(".json") and "\\cnc\\.tracker\\" in normalized

    @staticmethod
    def _is_cnc_path(file_path: str) -> bool:
        normalized = file_path.replace('/', '\\').lower()
        return "\\cnc\\" in normalized or normalized.endswith("\\cnc")

    def _run_tracker_scan(self, reason: str, src_path: str):
        if self.config.bad_parts_mode != "tracker":
            return
        if self.tracker_monitor is None:
            main_logger.warning("Tracker scan requested but tracker monitor is unavailable.")
            return
        try:
            events = self.tracker_monitor.scan_once()
            if events and self.alert_coordinator is not None:
                self.alert_coordinator.submit_events(events)
            job_folder_name = derive_job_from_tracker_path(self.config, src_path)
            if job_folder_name and self.deployment_gate is not None:
                state = self.deployment_gate.load_state(job_folder_name, default_deployed=True)
                if not bool(state.get("deployed", True)):
                    main_logger.info("Skipping tracker scan refresh for pending job: %s", job_folder_name)
                    return
            if job_folder_name:
                refresh_unresolved_bad_parts_for_job(self.config, job_folder_name, deployment_gate=self.deployment_gate)
            else:
                refresh_unresolved_bad_parts_all(self.config, deployment_gate=self.deployment_gate)
            main_logger.info(
                "Tracker scan finished (%s): new_events=%s active_total=%s source=%s",
                reason,
                len(events),
                len(self.tracker_monitor.state.active_keys),
                src_path,
            )
        except Exception as e:
            main_logger.error(f"Tracker scan failed ({reason}) for {src_path}: {e}", exc_info=True)

    def _trigger_tracker_scan(self, reason: str, src_path: str):
        if self.config.bad_parts_mode != "tracker":
            return
        delay_seconds = 0.6
        with self._tracker_scan_lock:
            if self._tracker_scan_timer is not None:
                self._tracker_scan_timer.cancel()
            self._tracker_scan_timer = threading.Timer(
                delay_seconds,
                lambda: self._run_tracker_scan(reason, src_path)
            )
            self._tracker_scan_timer.name = "TrackerScanDebounceTimer"
            self._tracker_scan_timer.daemon = True
            self._tracker_scan_timer.start()

    def _run_index_refresh(self, pdf_path: str, reason: str):
        if self.deployment_gate is not None:
            folder = os.path.dirname(pdf_path)
            if os.path.basename(folder).upper() == "DARK MODE":
                job_folder = os.path.dirname(folder)
            else:
                job_folder = folder
            if not self.deployment_gate.should_process_job_folder(job_folder):
                main_logger.info(
                    "Skipping index refresh for pending job (%s): %s",
                    reason,
                    pdf_path,
                )
                return
        try:
            build_reference_index_for_pdf_event(pdf_path)
        except Exception as e:
            main_logger.error(f"Reference index refresh failed ({reason}): {pdf_path} ({e})", exc_info=True)
        try:
            build_hardwoods_cutlist_index_for_pdf_event(pdf_path, deployment_gate=self.deployment_gate)
        except Exception as e:
            main_logger.error(f"Hardwoods cutlist index refresh failed ({reason}): {pdf_path} ({e})", exc_info=True)
        self._schedule_metadata_refresh(pdf_path, "index_refresh_complete")

    def _schedule_index_refresh(self, pdf_path: str, reason: str):
        def _timer_callback():
            try:
                self._run_index_refresh(pdf_path, reason)
            finally:
                with self._index_reparse_lock:
                    self._index_reparse_timers.pop(pdf_path, None)

        with self._index_reparse_lock:
            existing = self._index_reparse_timers.get(pdf_path)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._index_reparse_delay_seconds, _timer_callback)
            timer.name = f"IndexReparse-{os.path.basename(pdf_path)}"
            timer.daemon = True
            self._index_reparse_timers[pdf_path] = timer
            timer.start()
        main_logger.info(
            "Scheduled index re-parse in %ss (%s): %s",
            self._index_reparse_delay_seconds,
            reason,
            os.path.basename(pdf_path),
        )

    def _cleanup_old_cooldown_entries(self):
        """Remove cooldown entries older than 1 hour to prevent dictionary from growing indefinitely."""
        current_time = time.time()
        one_hour_ago = current_time - 3600

        # Remove entries older than 1 hour (with lock protection)
        with self._cooldown_lock:
            old_entries = [path for path, timestamp in self._conversion_cooldown.items() if timestamp < one_hour_ago]
            for path in old_entries:
                del self._conversion_cooldown[path]

        if old_entries:
            main_logger.debug(f"Cleaned up {len(old_entries)} old cooldown entries")

    def _schedule_pdf_conversion(self, pdf_path: str, invert_images: bool, delay_seconds: Optional[float] = None):
        """
        Schedule a PDF conversion after the cooldown period.

        Args:
            pdf_path (str): Full path to the PDF.
            invert_images (bool): Whether images should be inverted during conversion.
        """
        # Periodic cleanup every 100 conversions (thread-safe)
        with self._count_lock:
            self._conversion_count += 1
            should_cleanup = self._conversion_count % 100 == 0

        if should_cleanup:
            self._cleanup_old_cooldown_entries()

        delay_seconds = self._cooldown_seconds if delay_seconds is None else max(0.0, float(delay_seconds))
        main_logger.info(f"Scheduling PDF conversion in {delay_seconds}s: {os.path.basename(pdf_path)}")

        def _convert_task():
            try:
                main_logger.info(f"Wait complete, starting conversion: {os.path.basename(pdf_path)}")

                # Check if file still exists before converting
                if not os.path.exists(pdf_path):
                    if self._is_root_available():
                        main_logger.warning(f"PDF no longer exists, skipping conversion: {pdf_path}")
                        if self.pending_queue:
                            self.pending_queue.remove_pending_pdf(pdf_path)
                    else:
                        self._reschedule_pending_pdf_conversion(pdf_path, invert_images, delay_seconds=60.0)
                    return

                # Re-check deployment state at execution time to block conversions
                # while jobs are pending deployment.
                if self.deployment_gate is not None:
                    job_folder = self._resolve_job_folder_for_pdf(pdf_path)
                    if not self.deployment_gate.should_process_job_folder(job_folder):
                        main_logger.info(
                            "Skipping delayed dark mode conversion for pending job (awaiting deploy): %s",
                            pdf_path,
                        )
                        self._reschedule_pending_pdf_conversion(
                            pdf_path,
                            invert_images,
                            delay_seconds=max(60.0, float(self._cooldown_seconds)),
                        )
                        return

                # Remove from persistent queue when conversion runs
                if self.pending_queue:
                    self.pending_queue.remove_pending_pdf(pdf_path)

                # Run the conversion (synchronous, we're already in a background thread)
                from .pdf_dark_mode import run_dark_mode_conversion
                run_dark_mode_conversion(specific_file=pdf_path, invert_images=invert_images)
            except Exception as e:
                main_logger.error(f"Error in delayed PDF conversion thread for {pdf_path}: {e}", exc_info=True)
                if self._is_root_available():
                    if self.pending_queue:
                        self.pending_queue.remove_pending_pdf(pdf_path)
                else:
                    self._reschedule_pending_pdf_conversion(pdf_path, invert_images, delay_seconds=60.0)

        def _timer_callback():
            # Submit actual processing to executor or run in thread
            if self.executor:
                self.executor.submit(_convert_task)
            else:
                thread = threading.Thread(target=_convert_task, daemon=True, name=f"DelayedPDFConvert-{os.path.basename(pdf_path)}")
                thread.start()

        main_logger.debug(f"Waiting {delay_seconds}s before converting: {os.path.basename(pdf_path)}")
        # Use a timer to wait outside of the thread pool
        timer = threading.Timer(delay_seconds, _timer_callback)
        timer.name = f"Timer-PDFConvert-{os.path.basename(pdf_path)}"
        timer.daemon = True
        timer.start()

    def _schedule_dae_conversion(self, dae_path: str):
        """Schedule a DAE→GLB conversion after a short stabilisation delay."""
        delay_seconds = self._cooldown_seconds
        normalized_path = os.path.normcase(os.path.abspath(dae_path))

        def _convert_task():
            try:
                from pathlib import Path
                p = Path(dae_path)
                if not p.exists():
                    main_logger.warning(f"DAE no longer exists, skipping conversion: {dae_path}")
                    return
                convert_dae_to_medium_glb(p)
            except Exception as e:
                main_logger.error(f"DAE conversion failed for {dae_path}: {e}", exc_info=True)

        def _timer_callback():
            try:
                if self.executor:
                    self.executor.submit(_convert_task)
                else:
                    thread = threading.Thread(target=_convert_task, daemon=True, name=f"DaeConvert-{os.path.basename(os.path.dirname(dae_path))}")
                    thread.start()
            finally:
                with self._dae_reparse_lock:
                    self._dae_reparse_timers.pop(normalized_path, None)

        with self._dae_reparse_lock:
            existing = self._dae_reparse_timers.get(normalized_path)
            if existing is not None:
                existing.cancel()

            timer = threading.Timer(delay_seconds, _timer_callback)
            timer.name = f"Timer-DaeConvert-{os.path.basename(os.path.dirname(dae_path))}"
            timer.daemon = True
            self._dae_reparse_timers[normalized_path] = timer
            timer.start()

        main_logger.info(f"Scheduled DAE conversion in {delay_seconds}s: {dae_path}")

    def _should_convert_to_dark_mode(self, pdf_path: str) -> bool:
        """
        Check if a PDF should be converted to dark mode.

        Excludes CNC folders, DARK MODE folders, Cut List files, and files within pending job folders.

        Args:
            pdf_path (str): Full path to the PDF.

        Returns:
            bool: True if it should be converted, False otherwise.
        """
        # Normalize path separators
        normalized_path = pdf_path.replace('/', '\\')

        # Check if the filename belongs to the allowed list (case-insensitive)
        is_allowed = ALLOWED_SHEETS_PATTERN.search(os.path.basename(pdf_path))
        
        if not is_allowed:
            main_logger.debug(f"Skipping dark mode conversion for unapproved PDF type: {pdf_path}")
            return False

        # Check if the PDF is in a CNC subfolder
        if '\\CNC\\' in normalized_path or normalized_path.endswith('\\CNC'):
            return False

        # Check if the PDF is in a DARK MODE subfolder
        if '\\DARK MODE\\' in normalized_path:
            main_logger.debug(f"Skipping dark mode conversion for PDF already in DARK MODE folder: {pdf_path}")
            return False

        # Check if the PDF is in a pending folder (recently created, waiting for template updates)
        if self.rename_handler:
            # Get the job folder (parent of the PDF)
            pdf_dir = os.path.dirname(normalized_path)

            # Thread-safe access to pending folders
            with self.rename_handler._pending_folders_lock:
                pending_folders_snapshot = list(self.rename_handler._pending_folders.keys())

            # Check if this folder or any parent folder is pending
            for pending_folder in pending_folders_snapshot:
                normalized_pending = pending_folder.replace('/', '\\')
                if pdf_dir == normalized_pending or pdf_dir.startswith(normalized_pending + '\\'):
                    main_logger.debug(f"Skipping dark mode conversion for PDF in pending folder: {pdf_path}")
                    return False

        # Block dark mode conversion while a job is pending deployment.
        if self.deployment_gate is not None:
            job_folder = self._resolve_job_folder_for_pdf(pdf_path)
            if not self.deployment_gate.should_process_job_folder(job_folder):
                main_logger.info(
                    "Skipping dark mode conversion for pending job (awaiting deploy): %s",
                    pdf_path,
                )
                return False

        return True

    def on_modified(self, event):
        """
        Triggered when a PDF file is modified.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and self._is_watcher_refresh_signal(event.src_path):
                return
            if not event.is_directory:
                self._schedule_metadata_refresh(event.src_path, "modified")
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF modified event detected by recursive watcher: {event.src_path}")
                if self._is_cnc_path(event.src_path):
                    self._trigger_tracker_scan("pdf_modified", event.src_path)
                else:
                    self._schedule_index_refresh(event.src_path, "modified")

                # Convert specific PDF to dark mode (if not in CNC folder and cooldown elapsed)
                if self._should_convert_to_dark_mode(event.src_path):
                    current_time = time.time()

                    # Thread-safe cooldown check
                    with self._cooldown_lock:
                        last_conversion = self._conversion_cooldown.get(event.src_path, 0)
                        if current_time - last_conversion >= self._cooldown_seconds:
                            self._conversion_cooldown[event.src_path] = current_time
                            should_convert = True
                        else:
                            should_convert = False

                    if should_convert:
                        from .pdf_dark_mode import should_invert_images
                        invert = should_invert_images(event.src_path)
                        scheduled_time = current_time + self._cooldown_seconds
                        main_logger.info(f"Triggering dark mode conversion for modified PDF: {event.src_path} (invert_images={invert})")
                        main_logger.info(f"PDF conversion will run in {self._cooldown_seconds} seconds")

                        # Save to persistent queue
                        if self.pending_queue:
                            self.pending_queue.add_pending_pdf(event.src_path, scheduled_time, invert)

                        # Schedule the conversion
                        self._schedule_pdf_conversion(event.src_path, invert)
                    else:
                        main_logger.debug(f"Skipping dark mode conversion (cooldown active): {event.src_path}")
            elif not event.is_directory and self._is_tracker_stream_file(event.src_path):
                main_logger.debug(f"Tracker JSON modified event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_modified", event.src_path)
            elif not event.is_directory and os.path.basename(event.src_path).lower() == '3d.dae':
                self._schedule_dae_conversion(event.src_path)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_modified for {event.src_path}: {e}")

    def on_created(self, event):
        """
        Triggered when a PDF file is created.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and is_sync_conflict_path(event.src_path):
                resolve_sync_conflict_file(event.src_path, self.config.ROOT_DIR)
                return

            if not event.is_directory and self._is_watcher_refresh_signal(event.src_path):
                return

            if not event.is_directory:
                self._schedule_metadata_refresh(event.src_path, "created")
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF created event detected by recursive watcher: {event.src_path}")
                if self._is_cnc_path(event.src_path):
                    self._trigger_tracker_scan("pdf_created", event.src_path)
                else:
                    self._schedule_index_refresh(event.src_path, "created")

                # Convert specific PDF to dark mode (if not in CNC folder and cooldown elapsed)
                if self._should_convert_to_dark_mode(event.src_path):
                    current_time = time.time()

                    # Thread-safe cooldown check
                    with self._cooldown_lock:
                        last_conversion = self._conversion_cooldown.get(event.src_path, 0)
                        if current_time - last_conversion >= self._cooldown_seconds:
                            self._conversion_cooldown[event.src_path] = current_time
                            should_convert = True
                        else:
                            should_convert = False

                    if should_convert:
                        from .pdf_dark_mode import should_invert_images
                        invert = should_invert_images(event.src_path)
                        scheduled_time = current_time + self._cooldown_seconds
                        main_logger.info(f"Triggering dark mode conversion for created PDF: {event.src_path} (invert_images={invert})")
                        main_logger.info(f"PDF conversion will run in {self._cooldown_seconds} seconds")

                        # Save to persistent queue
                        if self.pending_queue:
                            self.pending_queue.add_pending_pdf(event.src_path, scheduled_time, invert)

                        # Schedule the conversion
                        self._schedule_pdf_conversion(event.src_path, invert)
                    else:
                        main_logger.debug(f"Skipping dark mode conversion (cooldown active): {event.src_path}")
            elif not event.is_directory and self._is_tracker_stream_file(event.src_path):
                main_logger.debug(f"Tracker JSON created event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_created", event.src_path)
            elif not event.is_directory and os.path.basename(event.src_path).lower() == '3d.dae':
                self._schedule_dae_conversion(event.src_path)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_created for {event.src_path}: {e}")

    def on_deleted(self, event):
        """
        Triggered when a PDF file is deleted. Cleans up associated files.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and self._is_watcher_refresh_signal(event.src_path):
                return
            if not event.is_directory:
                self._schedule_metadata_refresh(event.src_path, "deleted")
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF deleted event detected by recursive watcher: {event.src_path}")
                with self._index_reparse_lock:
                    existing = self._index_reparse_timers.pop(event.src_path, None)
                    if existing is not None:
                        existing.cancel()
                if not self._is_cnc_path(event.src_path):
                    folder = os.path.dirname(event.src_path)
                    if os.path.basename(folder).upper() == "DARK MODE":
                        job_folder = os.path.dirname(folder)
                    else:
                        job_folder = folder
                    if self.deployment_gate is not None and not self.deployment_gate.should_process_job_folder(job_folder):
                        main_logger.info("Skipping delete re-index for pending job: %s", event.src_path)
                        return
                    try:
                        build_reference_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Reference index refresh failed (deleted): {event.src_path} ({e})", exc_info=True)
                    try:
                        build_hardwoods_cutlist_index_for_pdf_event(event.src_path, deployment_gate=self.deployment_gate)
                    except Exception as e:
                        main_logger.error(f"Hardwoods cutlist index refresh failed (deleted): {event.src_path} ({e})", exc_info=True)

                # Normalize path
                normalized_path = event.src_path.replace('/', '\\')

                # If the deleted PDF is in a DARK MODE folder, do nothing
                if '\\DARK MODE\\' in normalized_path:
                    main_logger.debug(f"Deleted PDF was in DARK MODE folder, no cleanup needed: {event.src_path}")
                    return

                # If the deleted PDF is a light mode file, find and delete corresponding dark mode version
                pdf_dir = os.path.dirname(normalized_path)
                pdf_filename = os.path.basename(normalized_path)

                # Check for DARK MODE subfolder
                dark_mode_dir = os.path.join(pdf_dir, "DARK MODE")
                if os.path.exists(dark_mode_dir):
                    dark_mode_pdf = os.path.join(dark_mode_dir, pdf_filename)

                    if os.path.exists(dark_mode_pdf):
                        try:
                            os.remove(dark_mode_pdf)
                            main_logger.info(f"Deleted corresponding dark mode PDF: {dark_mode_pdf}")
                        except Exception as e:
                            main_logger.error(f"Failed to delete dark mode PDF {dark_mode_pdf}: {e}")
                    else:
                        main_logger.debug(f"No corresponding dark mode PDF found at: {dark_mode_pdf}")
                else:
                    main_logger.debug(f"No DARK MODE folder found at: {dark_mode_dir}")

                # Also remove from cooldown tracking to prevent memory leak (thread-safe)
                with self._cooldown_lock:
                    if event.src_path in self._conversion_cooldown:
                        del self._conversion_cooldown[event.src_path]
                        main_logger.debug(f"Removed deleted PDF from cooldown tracking: {event.src_path}")

                # Remove from pending queue if it was scheduled for conversion
                if self.pending_queue:
                    self.pending_queue.remove_pending_pdf(event.src_path)
            elif not event.is_directory and self._is_tracker_stream_file(event.src_path):
                main_logger.debug(f"Tracker JSON deleted event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_deleted", event.src_path)

        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_deleted for {event.src_path}: {e}")

class LogFileHandler(FileSystemEventHandler):
    """
    Handles modifications to the desktop bad parts log file.

    This handler is intentionally read-only. It does NOT mark/resolve parts.
    Resolution is owned by process_run_folders_v2.py.
    """
    def __init__(self, executor=None):
        """
        Initialize the LogFileHandler.

        Args:
            executor (ThreadPoolExecutor, optional): Executor for background task offloading.
        """
        super().__init__()
        self.executor = executor
        self._timer = None
        self._timer_lock = threading.Lock()

    def on_modified(self, event):
        """
        Triggered when the log file is modified. Offloads processing to a background task.
        Uses debouncing to wait 0.5s after the last modification before processing.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        if event.src_path == BAD_PART_LOG_FILE:
            with self._timer_lock:
                if self._timer is not None:
                    self._timer.cancel()

                # Debounce for 0.5 seconds to wait for file writes to finish
                self._timer = threading.Timer(0.5, self._submit_processing)
                self._timer.name = "LogFileDebounceTimer"
                self._timer.start()

    def _submit_processing(self):
        """Submit the actual processing task."""
        if self.executor:
            self.executor.submit(self._process_log_file)
        else:
            thread = threading.Thread(target=self._process_log_file, daemon=True, name="LogFileProcessing")
            thread.start()

    def _process_log_file(self):
        """Read-only observer for legacy log changes; does not mutate any state."""
        # Try to acquire lock without blocking to avoid redundant concurrent processing
        if not IS_PROCESSING_LOG_FILE_LOCK.acquire(blocking=False):
            main_logger.debug("Already processing log file, skipping.")
            return

        try:
            main_logger.info(
                "Observed legacy bad-parts log change at %s (read-only mode; no resolve/mutation performed).",
                BAD_PART_LOG_FILE,
            )

        except Exception as e:
            main_logger.error(f"Error processing log file: {e}", exc_info=True)
        finally:
            IS_PROCESSING_LOG_FILE_LOCK.release()
