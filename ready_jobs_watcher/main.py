"""
Core Application Module for Ready Jobs Watcher.

This module contains the primary Application class responsible for coordinating
file watchers, background tasks, GUI elements, and the system tray icon.
"""
import os
import sys
import threading
import logging
import time
import atexit

from watchdog.observers import Observer
from concurrent.futures import ThreadPoolExecutor


from .config import Config, BASE_DATA_DIR
from .file_handler import JobProcessor
from .utils import clear_old_logs, is_hidden
from .bad_parts_checker import load_blacklist, load_permanently_ignored_blacklist
from .watchers import RenameHandler, PdfChangeHandler, LogFileHandler
from .scheduler import backup_scheduler, cnc_scan_scheduler, stats_logger_scheduler, daily_restart_scheduler
from PyQt6.QtWidgets import QApplication
from .gui import SettingsWindow
from .tray_icon import create_tray_icon
from .planka_credentials import initialize_planka_credentials
from .tracker_bad_parts import TrackerBadPartsMonitor
from .alert_coordinator import AlertCoordinator, AlertBatch
from .cabinet_sheet_indexer import build_reference_index_for_job
from .hardwoods_cutlist_indexer import build_hardwoods_cutlist_index_for_job


# --- Logging Setup ---
def setup_logging():
    """
    Configure multiple loggers for distinct application components.

    Returns:
        logging.Logger: The configured main logger instance.
    """
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    loggers = {
        'main': 'ready_jobs_watcher.log',
        'backup': 'backup.log',
        'cnc': 'cnc_scan.log',
        'badparts': 'bad_parts.log',
        'planka': 'planka.log',
        'pdf_darkmode': 'send_notification.log',
        'pending_queue': 'ready_jobs_watcher.log'
    }

    for name, filename in loggers.items():
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, filename))
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Console handler for higher-level info
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    logging.getLogger('main').addHandler(console)

    return logging.getLogger('main')

# --- Core Application Class ---
class Application:
    """
    Main application controller managing background threads, observers, and state.

    Responsibilities include initializing the file system observers, coordinating
    configuration state, executing scheduled tasks, and properly acquiring system locks.
    """
    def __init__(self):
        """Initialize the Application instance with default state and component setup."""
        self.config = Config()
        self.stop_event = threading.Event()

        self.PAUSE_PROCESSING = False
        self.PENDING_RENAMES = {}
        self.pending_renames_lock = threading.Lock()  # Lock for thread-safe access to PENDING_RENAMES
        self.LAST_BACKUP_TIME = None

        # Thread pool for background operations (prevents unbounded thread creation)
        self.executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="RJW-Worker")

        self.job_processor = JobProcessor(self.config, self)

        # Initialize persistent pending queue
        from .pending_queue import PendingQueue
        queue_file = os.path.join(BASE_DATA_DIR, 'pending_queue.json')
        self.pending_queue = PendingQueue(queue_file, executor=self.executor)

        self.observer = Observer()
        self.pdf_observer = Observer()
        self.desktop_observer = Observer()

        self.retry_thread = None
        self.backup_thread = None
        self.cnc_scan_thread = None
        self.stats_thread = None
        self.restart_thread = None
        self.tray_thread = None

        self.qapp = None
        self.settings_window = None
        self.icon = None
        self._pending_alert_batches = []
        self._pending_alert_lock = threading.Lock()
        self.tracker_monitor = TrackerBadPartsMonitor(self.config)
        self.alert_coordinator = AlertCoordinator(
            self.config,
            self.tracker_monitor,
            popup_notifier=self._queue_bad_parts_popup
        )

    def _queue_bad_parts_popup(self, batch: AlertBatch):
        if self.settings_window:
            self.settings_window.emit_bad_parts_alert(batch)
            return
        with self._pending_alert_lock:
            self._pending_alert_batches.append(batch)

    def _flush_pending_bad_parts_popups(self):
        if not self.settings_window:
            return
        with self._pending_alert_lock:
            batches = list(self._pending_alert_batches)
            self._pending_alert_batches.clear()
        for batch in batches:
            self.settings_window.emit_bad_parts_alert(batch)

    def start(self):
        """Initializes and starts all application components."""
        os.makedirs(self.config.BACKUP_DIR, exist_ok=True)
        os.makedirs(BASE_DATA_DIR, exist_ok=True)

        clear_old_logs()

        if not self.acquire_lock():
            logging.warning("Another instance is already running. Exiting.")
            sys.exit(0)

        # Initialize Planka credentials from config and keyring
        initialize_planka_credentials(self.config)

        if self.config.bad_parts_mode == "legacy":
            load_blacklist()
            load_permanently_ignored_blacklist()
        else:
            logging.info("Tracker bad-parts mode enabled; legacy PDF-highlight blacklists are not loaded.")

        self.alert_coordinator.start()

        self.start_threads()
        self.start_observers()

        self.setup_gui()

    def stop(self):
        """Stops all threads and observers gracefully."""
        logging.info("Shutting down application...")
        self.stop_event.set()

        # Stop observers and wait for them to finish
        observers = [
            ('main_observer', self.observer),
            ('pdf_observer', self.pdf_observer),
            ('desktop_observer', self.desktop_observer)
        ]

        for name, obs in observers:
            if obs and obs.is_alive():
                try:
                    obs.stop()
                except Exception as e:
                    logging.error(f"Error stopping {name}: {e}")

        for name, obs in observers:
            if obs and obs.is_alive():
                try:
                    obs.join(timeout=5)
                except RuntimeError as e:
                    logging.error(f"Error joining {name}: {e}")
                if obs.is_alive():
                    logging.warning(f"{name} did not stop within timeout")

        # Wait for background threads
        threads = [
            ('retry_thread', self.retry_thread),
            ('backup_thread', self.backup_thread),
            ('cnc_scan_thread', self.cnc_scan_thread),
            ('stats_thread', self.stats_thread),
            ('restart_thread', self.restart_thread),
            ('tray_thread', self.tray_thread)
        ]

        for name, thread in threads:
            if thread and thread.is_alive():
                try:
                    thread.join(timeout=5)
                except RuntimeError as e:
                    logging.error(f"Error joining {name}: {e}")
                if thread.is_alive():
                    logging.warning(f"{name} did not stop within timeout")

        # Stop thread pool executor
        if self.executor:
            try:
                logging.info("Shutting down thread pool executor...")
                self.executor.shutdown(wait=True, cancel_futures=False)
                logging.info("Thread pool executor shut down successfully")
            except Exception as e:
                logging.error(f"Error shutting down executor: {e}")

        if self.alert_coordinator:
            try:
                self.alert_coordinator.stop()
            except Exception as e:
                logging.error(f"Error stopping alert coordinator: {e}")

        # Stop tray icon and GUI
        if self.icon:
            try:
                self.icon.hide()
            except Exception as e:
                logging.error(f"Error stopping tray icon: {e}")

        if hasattr(self, 'qapp') and self.qapp:
            try:
                self.qapp.quit()
            except Exception as e:
                logging.error(f"Error quitting QApplication: {e}")

        logging.info("Shutdown complete.")

    def restart(self):
        """
        Safely restarts the application.

        This method stops background threads, explicitly releases the single-instance
        lock, starts a new process of the application, and then forcefully exits
        the current process to avoid Tkinter deadlocks during shutdown.
        """
        import subprocess

        logging.info("Initiating safe application restart...")

        # Stop background loops from doing more work
        self.stop_event.set()

        # Give pending operations a moment to save
        time.sleep(2)

        # Cleanly hide the tray icon to prevent ghost icons on Windows
        if self.icon:
            try:
                self.icon.stop()
            except Exception as e:
                logging.error(f"Error stopping tray icon during restart: {e}")

        # Release the lock file so the new instance doesn't think we're still running
        self.release_lock()

        # Start a new instance
        try:
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                executable = sys.executable
                args = [executable]
            else:
                # Running as script
                executable = sys.executable
                args = [executable] + sys.argv

            # Use subprocess.Popen to start a new process
            # DETACHED_PROCESS flag ensures the new process is independent
            if os.name == 'nt':
                # Windows
                DETACHED_PROCESS = 0x00000008
                subprocess.Popen(
                    args,
                    creationflags=DETACHED_PROCESS,
                    close_fds=True,
                    start_new_session=True
                )
            else:
                # Unix-like
                subprocess.Popen(
                    args,
                    start_new_session=True,
                    close_fds=True
                )

            logging.info("New instance started. Exiting current process forcefully.")

            # Force exit completely bypassing Tkinter cleanup which deadlocks on background threads
            os._exit(0)

        except Exception as e:
            logging.error(f"Failed to spawn new process during restart: {e}")
            # If we failed to spawn, try to restore normal operation by un-setting the stop event
            self.stop_event.clear()
            # Try to re-acquire the lock
            self.acquire_lock()
            # Restart tray icon
            if self.settings_window and self.config:
                self.icon = create_tray_icon(self.settings_window, self.config, self)
                self.tray_thread = threading.Thread(target=self.icon.run, daemon=True)
                self.tray_thread.start()

    def _is_process_running(self, pid):
        """
        Check if a process with given PID is running.

        Args:
            pid (int): The process ID to check.

        Returns:
            bool: True if running, False otherwise.
        """
        try:
            # On Windows, try to open the process
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # If we can't check, assume it's not running
            return False

    def acquire_lock(self):
        """
        Acquires a single-instance lock using PID-based locking.
        This is more robust than file locking and survives crashes.

        Returns:
            bool: True if lock was acquired, False if another instance exists.
        """
        lock_file = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
        current_pid = os.getpid()

        try:
            # Check if lock file exists
            if os.path.exists(lock_file):
                try:
                    with open(lock_file, 'r') as f:
                        existing_pid = int(f.read().strip())

                    # Check if the process with that PID is still running
                    if self._is_process_running(existing_pid):
                        logging.warning(f"Another instance is already running (PID: {existing_pid}). Exiting.")
                        return False
                    else:
                        logging.info(f"Found stale lock file from PID {existing_pid}, removing it.")
                        os.remove(lock_file)
                except (ValueError, IOError) as e:
                    logging.warning(f"Invalid or unreadable lock file, removing it: {e}")
                    try:
                        os.remove(lock_file)
                    except Exception:
                        pass

            # Write our PID to the lock file
            with open(lock_file, 'w') as f:
                f.write(str(current_pid))

            # Register cleanup handler
            atexit.register(self.release_lock)

            logging.info(f"Acquired single instance lock (PID: {current_pid}).")
            return True

        except Exception as e:
            logging.error(f"Failed to acquire lock: {e}")
            return False

    def release_lock(self):
        """Releases the single-instance lock by removing the PID file."""
        lock_file = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
        try:
            if os.path.exists(lock_file):
                # Verify it's our PID before removing
                try:
                    with open(lock_file, 'r') as f:
                        existing_pid = int(f.read().strip())
                    if existing_pid == os.getpid():
                        os.remove(lock_file)
                        logging.info("Released single instance lock.")
                    else:
                        logging.warning(f"Lock file contains different PID ({existing_pid} vs {os.getpid()}), not removing.")
                except Exception:
                    # If we can't read it, just try to remove it
                    os.remove(lock_file)
                    logging.info("Released single instance lock.")
        except Exception as e:
            logging.error(f"Error releasing lock: {e}")

    def start_threads(self):
        """Starts the background threads for retries and scheduled tasks."""
        self.retry_thread = threading.Thread(target=self.retry_pending, daemon=True)
        self.retry_thread.start()

        self.backup_thread = threading.Thread(target=backup_scheduler, args=(self.config, self.stop_event, self), daemon=True)
        self.backup_thread.start()

        self.cnc_scan_thread = threading.Thread(
            target=cnc_scan_scheduler,
            args=(self.config, self.stop_event, self.tracker_monitor, self.alert_coordinator),
            daemon=True
        )
        self.cnc_scan_thread.start()

        self.stats_thread = threading.Thread(target=stats_logger_scheduler, args=(self.stop_event,), daemon=True)
        self.stats_thread.start()

        self.restart_thread = threading.Thread(target=daily_restart_scheduler, args=(self.config, self.stop_event, self), daemon=True)
        self.restart_thread.start()
        logging.info(f"Daily restart scheduled for {self.config.daily_restart_time}")

    def restore_pending_operations(self, rename_handler, pdf_handler):
        """
        Restore pending operations from the queue after a restart.

        Args:
            rename_handler (RenameHandler): Event handler for tracking folder changes.
            pdf_handler (PdfChangeHandler): Event handler for scheduling PDF conversions.
        """
        # Use the improved resume method from PendingQueue which properly handles delays and uses synchronous conversion
        self.pending_queue.resume_pending_operations(pdf_handler, rename_handler)

    def start_observers(self):
        """Starts the filesystem watchers."""
        event_handler = RenameHandler(self.config, self.job_processor, self, pending_queue=self.pending_queue, executor=self.executor)
        self.observer.schedule(event_handler, self.config.ROOT_DIR, recursive=True)
        self.observer.start()
        logging.info(f"Watching {self.config.ROOT_DIR} for folder changes...")

        # Pass rename_handler reference so PDF handler can check for pending folders
        pdf_event_handler = PdfChangeHandler(
            self.config,
            rename_handler=event_handler,
            pending_queue=self.pending_queue,
            executor=self.executor,
            tracker_monitor=self.tracker_monitor,
            alert_coordinator=self.alert_coordinator
        )
        self.pdf_observer.schedule(pdf_event_handler, self.config.ROOT_DIR, recursive=True)
        self.pdf_observer.start()
        logging.info(f"Watching {self.config.ROOT_DIR} for PDF changes...")

        # Restore pending operations from last session
        self.restore_pending_operations(event_handler, pdf_event_handler)

        if self.config.bad_parts_mode == "legacy":
            desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
            log_file_handler = LogFileHandler(executor=self.executor)
            self.desktop_observer.schedule(log_file_handler, desktop_path, recursive=False)
            self.desktop_observer.start()
            logging.info(f"Watching {desktop_path} for log file changes...")
        else:
            logging.info("Tracker mode enabled; desktop bad-parts log watcher is disabled.")

    def setup_gui(self):
        """Sets up the PyQt6 QApplication, settings window, and system tray icon."""
        # QApplication must be created before any QWidget or QSystemTrayIcon
        if not QApplication.instance():
            self.qapp = QApplication(sys.argv)
        else:
            self.qapp = QApplication.instance()

        self.qapp.setQuitOnLastWindowClosed(False)

        self.settings_window = SettingsWindow(self.config, self)
        self.settings_window.set_alert_coordinator(self.alert_coordinator)
        self.icon = create_tray_icon(self.settings_window, self.config, self)
        self.icon.show()
        self._flush_pending_bad_parts_popups()

        # We start the initial scan slightly delayed similar to Tkinter's after
        import threading
        threading.Timer(0.1, self.initial_scan).start()

        # Start the event loop
        sys.exit(self.qapp.exec())

    def perform_backup(self):
        """Execute an immediate manual backup based on configured paths."""
        from .scheduler import perform_backup
        perform_backup(self.config, self)

    def scan_cnc_pdfs_for_bad_parts(self):
        """Trigger an immediate scan of all existing CNC PDFs."""
        from .scheduler import scan_cnc_pdfs_for_bad_parts
        scan_cnc_pdfs_for_bad_parts(self.config, self.tracker_monitor, self.alert_coordinator)

    def initial_scan(self):
        """Performs an initial scan of the root directory to handle any backlog."""
        if self.PAUSE_PROCESSING:
            logging.info("Initial scan skipped (GUI open).")
            return
        logging.info("Starting initial scan...")
        try:
            with os.scandir(self.config.ROOT_DIR) as it:
                for entry in it:
                    if entry.is_dir():
                        full_path = entry.path
                        if is_hidden(full_path):
                            logging.info(f"Skipping hidden item: {full_path}")
                            continue
                        self.job_processor.process_job_folder(full_path)
                        try:
                            build_reference_index_for_job(full_path)
                        except Exception as e:
                            logging.error(f"Reference index build failed for {full_path}: {e}", exc_info=True)
                        try:
                            build_hardwoods_cutlist_index_for_job(full_path)
                        except Exception as e:
                            logging.error(f"Hardwoods cutlist index build failed for {full_path}: {e}", exc_info=True)
                        time.sleep(0.05)
        except OSError as e:
            logging.error(f"Error during initial scan: {e}")
        logging.info("Initial scan complete.")

    def retry_pending(self):
        """Periodically retries failed file rename operations using a background thread."""
        while not self.stop_event.is_set():
            if self.PAUSE_PROCESSING:
                self.stop_event.wait(5)
                continue

            current_time = time.time()
            to_remove = []

            # Create a snapshot with lock protection
            with self.pending_renames_lock:
                pending_snapshot = dict(self.PENDING_RENAMES)

            for old_path, (job_num, dir_path, original_name, next_retry) in pending_snapshot.items():
                if current_time >= next_retry:
                    # Retry the rename operation
                    new_name = job_num + ' - ' + original_name
                    new_path = os.path.join(dir_path, new_name)
                    try:
                        # Check if source file exists
                        if not os.path.exists(old_path):
                            logging.warning(f"Retry skipped: {old_path} no longer exists.")
                            to_remove.append(old_path)
                            continue

                        # Check if destination already exists (might have been renamed manually)
                        if os.path.exists(new_path):
                            logging.info(f"Retry skipped: destination already exists: {new_path}")
                            to_remove.append(old_path)
                            continue

                        os.rename(old_path, new_path)
                        logging.info(f"Retry successful: {old_path} -> {new_path}")
                        to_remove.append(old_path)
                    except PermissionError:
                        # Still locked, reschedule
                        next_retry_time = time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60)
                        with self.pending_renames_lock:
                            self.PENDING_RENAMES[old_path] = (job_num, dir_path, original_name, next_retry_time)
                        logging.warning(f"Retry failed (still locked): {old_path}. Will retry later.")
                    except FileNotFoundError:
                        logging.warning(f"Retry failed: file disappeared: {old_path}")
                        to_remove.append(old_path)
                    except OSError as e:
                        # Handle sharing violations (file in use)
                        if hasattr(e, 'winerror') and e.winerror == 32:
                            next_retry_time = time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60)
                            with self.pending_renames_lock:
                                self.PENDING_RENAMES[old_path] = (job_num, dir_path, original_name, next_retry_time)
                            logging.warning(f"Retry failed (file in use): {old_path}. Will retry later.")
                        else:
                            logging.error(f"Retry failed for {old_path}: {e}")
                            to_remove.append(old_path)
                    except Exception as e:
                        logging.error(f"Retry failed for {old_path}: {e}")
                        to_remove.append(old_path)

            # Remove successfully renamed files from pending (with lock protection)
            with self.pending_renames_lock:
                for path in to_remove:
                    self.PENDING_RENAMES.pop(path, None)

            self.stop_event.wait(60)

# --- Entry Point ---
if __name__ == "__main__":
    setup_logging()
    from . import __version__
    logging.info(f"Ready Jobs Watcher v{__version__} starting...")
    app = Application()
    try:
        app.start()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        app.stop()
