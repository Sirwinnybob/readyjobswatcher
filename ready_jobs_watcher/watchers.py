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

# This will be properly imported later
from .bad_parts_checker import (
    BAD_PART_LOG_FILE, BLACKLISTED_FILES, PERMANENTLY_IGNORED_FILES,
    save_to_blacklist_internal, save_permanently_ignored_blacklist_internal,
    BLACKLIST_LOCK, PERMANENTLY_IGNORED_LOCK
)
from .file_handler import should_ignore_folder, should_ignore_file
from .utils import is_hidden, ALLOWED_SHEETS_PATTERN
from .tracker_bad_parts import TrackerBadPartsMonitor
from .alert_coordinator import AlertCoordinator
from .cabinet_sheet_indexer import build_reference_index_for_pdf_event
from .hardwoods_cutlist_indexer import build_hardwoods_cutlist_index_for_pdf_event


main_logger = logging.getLogger('main')

# Thread-safe lock for log file processing
IS_PROCESSING_LOG_FILE_LOCK = threading.Lock()

class RenameHandler(FileSystemEventHandler):
    """
    Event handler for file and directory creation or movement events.

    Detects new job folders and files, scheduling them for delayed renaming
    and processing to ensure template files are not renamed prematurely.
    """
    def __init__(self, config, job_processor, app_state, pending_queue=None, executor=None):
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

    def _schedule_folder_processing(self, folder_path: str):
        """
        Schedule a folder to be processed after a configured delay.

        Args:
            folder_path (str): Full path to the detected folder.
        """
        scheduled_time = time.time() + self._folder_delay_seconds

        with self._pending_folders_lock:
            self._pending_folders[folder_path] = scheduled_time

        main_logger.info(f"Scheduled folder processing in {self._folder_delay_seconds}s: {folder_path}")

        # Save to persistent queue
        if self.pending_queue:
            self.pending_queue.add_pending_folder(folder_path, scheduled_time)

        # Actual task to process the folder
        def _process_task():
            try:
                # Check if still scheduled (might have been cancelled)
                should_process = False
                with self._pending_folders_lock:
                    if folder_path in self._pending_folders:
                        main_logger.info(f"Processing delayed folder: {folder_path}")
                        del self._pending_folders[folder_path]
                        should_process = True
                    else:
                        main_logger.debug(f"Folder processing was cancelled: {folder_path}")

                if should_process:
                    # Remove from persistent queue
                    if self.pending_queue:
                        self.pending_queue.remove_pending_folder(folder_path)
                    self.job_processor.process_job_folder(folder_path)
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
        timer = threading.Timer(self._folder_delay_seconds, _timer_callback)
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
            main_logger.debug(f"on_created triggered for {event.src_path}")
            if self.app_state.PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring created event for {event.src_path}")
                return

            # Skip hidden files/folders
            if is_hidden(event.src_path):
                main_logger.debug(f"Skipping hidden item: {event.src_path}")
                return

            if event.is_directory:
                folder_name = os.path.basename(event.src_path)

                # Skip template folders (Face Frame, Frameless) until renamed
                if should_ignore_folder(folder_name):
                    main_logger.debug(f"Skipping template folder: {event.src_path}")
                    return

                # New job folder created - schedule processing after delay
                main_logger.info(f"New folder created: {event.src_path}")
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
            main_logger.debug(f"on_modified triggered for {event.src_path}")
            if self.app_state.PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring modified event for {event.src_path}")
                return
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_modified for {event.src_path}: {e}")

    def on_moved(self, event):
        """
        Triggered when a file or directory is moved or renamed.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            main_logger.debug(f"on_moved triggered for {event.src_path} -> {event.dest_path}")
            if self.app_state.PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring moved event for {event.src_path} -> {event.dest_path}")
                return

            # Skip hidden destinations
            if is_hidden(event.dest_path):
                main_logger.debug(f"Skipping hidden destination: {event.dest_path}")
                return

            main_logger.info(f"Moved/renamed event detected: {event.src_path} -> {event.dest_path} (is_directory={event.is_directory})")
            if event.is_directory:
                folder_name = os.path.basename(event.dest_path)

                # Skip template folders
                if should_ignore_folder(folder_name):
                    main_logger.debug(f"Skipping template folder: {event.dest_path}")
                    return

                self.job_processor.process_job_folder(event.dest_path, include_cnc=True)
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
        alert_coordinator: Optional[AlertCoordinator] = None
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

    @staticmethod
    def _is_tracker_json(file_path: str) -> bool:
        normalized = file_path.replace('/', '\\').lower()
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

    def _schedule_pdf_conversion(self, pdf_path: str, invert_images: bool):
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

        delay_seconds = self._cooldown_seconds
        main_logger.info(f"Scheduling PDF conversion in {delay_seconds}s: {os.path.basename(pdf_path)}")

        def _convert_task():
            try:
                main_logger.info(f"Wait complete, starting conversion: {os.path.basename(pdf_path)}")

                # Check if file still exists before converting
                if not os.path.exists(pdf_path):
                    main_logger.warning(f"PDF no longer exists, skipping conversion: {pdf_path}")
                    if self.pending_queue:
                        self.pending_queue.remove_pending_pdf(pdf_path)
                    return

                # Remove from persistent queue when conversion runs
                if self.pending_queue:
                    self.pending_queue.remove_pending_pdf(pdf_path)

                # Run the conversion (synchronous, we're already in a background thread)
                from .pdf_dark_mode import run_dark_mode_conversion
                run_dark_mode_conversion(specific_file=pdf_path, invert_images=invert_images)
            except Exception as e:
                main_logger.error(f"Error in delayed PDF conversion thread for {pdf_path}: {e}", exc_info=True)

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

        return True

    def on_modified(self, event):
        """
        Triggered when a PDF file is modified.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF modified event detected by recursive watcher: {event.src_path}")
                if self._is_cnc_path(event.src_path):
                    self._trigger_tracker_scan("pdf_modified", event.src_path)
                else:
                    try:
                        build_reference_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Reference index refresh failed (modified): {event.src_path} ({e})", exc_info=True)
                    try:
                        build_hardwoods_cutlist_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Hardwoods cutlist index refresh failed (modified): {event.src_path} ({e})", exc_info=True)

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
            elif not event.is_directory and self._is_tracker_json(event.src_path):
                main_logger.debug(f"Tracker JSON modified event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_modified", event.src_path)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_modified for {event.src_path}: {e}")

    def on_created(self, event):
        """
        Triggered when a PDF file is created.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF created event detected by recursive watcher: {event.src_path}")
                if self._is_cnc_path(event.src_path):
                    self._trigger_tracker_scan("pdf_created", event.src_path)
                else:
                    try:
                        build_reference_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Reference index refresh failed (created): {event.src_path} ({e})", exc_info=True)
                    try:
                        build_hardwoods_cutlist_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Hardwoods cutlist index refresh failed (created): {event.src_path} ({e})", exc_info=True)

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
            elif not event.is_directory and self._is_tracker_json(event.src_path):
                main_logger.debug(f"Tracker JSON created event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_created", event.src_path)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_created for {event.src_path}: {e}")

    def on_deleted(self, event):
        """
        Triggered when a PDF file is deleted. Cleans up associated files.

        Args:
            event (FileSystemEvent): The watchdog event instance.
        """
        try:
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF deleted event detected by recursive watcher: {event.src_path}")
                if not self._is_cnc_path(event.src_path):
                    try:
                        build_reference_index_for_pdf_event(event.src_path)
                    except Exception as e:
                        main_logger.error(f"Reference index refresh failed (deleted): {event.src_path} ({e})", exc_info=True)
                    try:
                        build_hardwoods_cutlist_index_for_pdf_event(event.src_path)
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
            elif not event.is_directory and self._is_tracker_json(event.src_path):
                main_logger.debug(f"Tracker JSON deleted event detected: {event.src_path}")
                self._trigger_tracker_scan("tracker_deleted", event.src_path)

        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_deleted for {event.src_path}: {e}")

class LogFileHandler(FileSystemEventHandler):
    """
    Handles modifications to the bad parts log file on the desktop.

    When user marks a bad part as complete by appending 'y' after 'COMPLETE:'
    in a log line, this moves the entry from temporary blacklist to permanent ignore.
    Log format expected: "filename | full_path | page_num | Reported: timestamp | COMPLETE: "
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
        """Core logic for processing the bad parts log file in a background thread."""
        # Try to acquire lock without blocking to avoid redundant concurrent processing
        if not IS_PROCESSING_LOG_FILE_LOCK.acquire(blocking=False):
            main_logger.debug("Already processing log file, skipping.")
            return

        try:
            main_logger.info(f"Processing change in {BAD_PART_LOG_FILE}...")

            if not os.path.exists(BAD_PART_LOG_FILE):
                main_logger.warning(f"Log file {BAD_PART_LOG_FILE} no longer exists.")
                return

            with open(BAD_PART_LOG_FILE, 'r') as f:
                lines = f.readlines()

            active_entries = []
            has_changes = False

            for line in lines:
                parts = line.split('|')
                # Expecting 5 parts: filename | full_path | page_num | Reported: timestamp | COMPLETE:
                if len(parts) >= 5 and parts[4].strip().lower().startswith("complete: y"):
                    full_path = parts[1].strip()
                    try:
                        page_num = int(parts[2].strip()) - 1  # Extract page number (0-indexed)
                    except ValueError:
                        main_logger.warning(f"Invalid page number in log line: {line.strip()}")
                        active_entries.append(line)
                        continue

                    # Remove from blacklist (thread-safe)
                    with BLACKLIST_LOCK:
                        if (full_path, page_num) in BLACKLISTED_FILES:
                            BLACKLISTED_FILES.remove((full_path, page_num))
                            save_to_blacklist_internal()
                            main_logger.info(f"Removed {full_path} (page {page_num}) from blacklist.")

                    # Add to permanently ignored blacklist (thread-safe)
                    with PERMANENTLY_IGNORED_LOCK:
                        PERMANENTLY_IGNORED_FILES.add((full_path, page_num))
                        save_permanently_ignored_blacklist_internal()
                        main_logger.info(f"Added {full_path} (page {page_num}) to permanently ignored blacklist.")

                    main_logger.info(f"'{full_path}' (page {page_num}) marked as complete. Removing from log.")
                    has_changes = True
                else:
                    active_entries.append(line)

            if has_changes:
                with open(BAD_PART_LOG_FILE, 'w') as f:
                    f.writelines(active_entries)
                main_logger.info("Bad parts log file updated.")

        except Exception as e:
            main_logger.error(f"Error processing log file: {e}", exc_info=True)
        finally:
            IS_PROCESSING_LOG_FILE_LOCK.release()
