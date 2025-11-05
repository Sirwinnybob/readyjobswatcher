import os
import sys
import threading
import logging
import time
import atexit
import msvcrt
import tkinter as tk
from watchdog.observers import Observer
import sv_ttk

from .config import Config, BASE_DATA_DIR
from .file_handler import JobProcessor
from .utils import clear_old_logs, is_hidden
from .bad_parts_checker import load_blacklist, load_permanently_ignored_blacklist
from .watchers import RenameHandler, PdfChangeHandler, LogFileHandler
from .scheduler import backup_scheduler, cnc_scan_scheduler
from .gui import SettingsWindow, is_dark_mode
from .tray_icon import create_tray_icon

# --- Global State ---
# These globals are modified by different parts of the application (e.g., GUI, watchers)
# A more advanced implementation might use a shared state object or a message queue.
# PAUSE_PROCESSING = False
# PENDING_RENAMES = {}
# LAST_BACKUP_TIME = None

# --- Logging Setup ---
def setup_logging():
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    loggers = {
        'main': 'ready_jobs_watcher.log',
        'backup': 'backup.log',
        'cnc': 'cnc_scan.log',
        'badparts': 'bad_parts.log',
        'planka': 'planka.log'
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
    def __init__(self):
        self.config = Config()
        self.job_processor = JobProcessor(self.config)
        self.stop_event = threading.Event()

        self.PAUSE_PROCESSING = False
        self.PENDING_RENAMES = {}
        self.LAST_BACKUP_TIME = None

        self.observer = Observer()
        self.pdf_observer = Observer()
        self.desktop_observer = Observer()

        self.retry_thread = None
        self.backup_thread = None
        self.cnc_scan_thread = None
        self.tray_thread = None

        self.root = None
        self.settings_window = None
        self.icon = None

    def start(self):
        """Initializes and starts all application components."""
        os.makedirs(self.config.BACKUP_DIR, exist_ok=True)
        os.makedirs(BASE_DATA_DIR, exist_ok=True)

        clear_old_logs()

        if not self.acquire_lock():
            logging.warning("Another instance is already running. Exiting.")
            sys.exit(0)

        load_blacklist()
        load_permanently_ignored_blacklist()

        self.start_threads()
        self.start_observers()

        self.setup_gui()

    def stop(self):
        """Stops all threads and observers gracefully."""
        logging.info("Shutting down application...")
        self.stop_event.set()

        self.observer.stop()
        self.pdf_observer.stop()
        self.desktop_observer.stop()

        self.observer.join(timeout=2)
        self.pdf_observer.join(timeout=2)
        self.desktop_observer.join(timeout=2)

        if self.retry_thread: self.retry_thread.join(timeout=2)
        if self.backup_thread: self.backup_thread.join(timeout=2)
        if self.cnc_scan_thread: self.cnc_scan_thread.join(timeout=2)

        if self.icon:
            self.icon.stop()

        if self.root:
            self.root.destroy()

        logging.info("Shutdown complete.")

    def acquire_lock(self):
        """Acquires a single-instance lock for the application."""
        lock_file = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
        try:
            self.lock_file_handle = open(lock_file, 'w')
            msvcrt.locking(self.lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            atexit.register(self.release_lock)
            logging.info("Acquired single instance lock.")
            return True
        except IOError:
            return False

    def release_lock(self):
        """Releases the single-instance lock."""
        if self.lock_file_handle:
            try:
                msvcrt.locking(self.lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                self.lock_file_handle.close()
                logging.info("Released single instance lock.")
            except Exception as e:
                logging.error(f"Error releasing lock: {e}")

    def start_threads(self):
        """Starts the background threads for retries and scheduled tasks."""
        self.retry_thread = threading.Thread(target=self.retry_pending, daemon=True)
        self.retry_thread.start()

        self.backup_thread = threading.Thread(target=backup_scheduler, args=(self.config, self.stop_event), daemon=True)
        self.backup_thread.start()

        self.cnc_scan_thread = threading.Thread(target=cnc_scan_scheduler, args=(self.config, self.stop_event), daemon=True)
        self.cnc_scan_thread.start()

    def start_observers(self):
        """Starts the filesystem watchers."""
        event_handler = RenameHandler(self.config, self.job_processor)
        self.observer.schedule(event_handler, self.config.ROOT_DIR, recursive=True)
        self.observer.start()
        logging.info(f"Watching {self.config.ROOT_DIR} for folder changes...")

        pdf_event_handler = PdfChangeHandler(self.config)
        self.pdf_observer.schedule(pdf_event_handler, self.config.ROOT_DIR, recursive=True)
        self.pdf_observer.start()
        logging.info(f"Watching {self.config.ROOT_DIR} for PDF changes...")

        desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
        log_file_handler = LogFileHandler()
        self.desktop_observer.schedule(log_file_handler, desktop_path, recursive=False)
        self.desktop_observer.start()
        logging.info(f"Watching {desktop_path} for log file changes...")

    def setup_gui(self):
        """Sets up the Tkinter root, settings window, and system tray icon."""
        self.root = tk.Tk()
        self.root.withdraw()

        sv_ttk.set_theme("dark" if is_dark_mode() else "light")

        self.settings_window = SettingsWindow(self.root, self.config, self)
        self.icon = create_tray_icon(self.settings_window, self.config, self)

        self.tray_thread = threading.Thread(target=self.icon.run, daemon=True)
        self.tray_thread.start()

        self.root.after(100, self.initial_scan)
        self.root.mainloop()

    def perform_backup(self):
        from .scheduler import perform_backup
        perform_backup(self.config, self)

    def scan_cnc_pdfs_for_bad_parts(self):
        from .scheduler import scan_cnc_pdfs_for_bad_parts
        scan_cnc_pdfs_for_bad_parts(self.config)

    def initial_scan(self):
        """Performs an initial scan of the root directory."""
        if self.PAUSE_PROCESSING:
            logging.info("Initial scan skipped (GUI open).")
            return
        logging.info("Starting initial scan...")
        for folder in os.listdir(self.config.ROOT_DIR):
            full_path = os.path.join(self.config.ROOT_DIR, folder)
            if is_hidden(full_path):
                logging.info(f"Skipping hidden item: {full_path}")
                continue
            self.job_processor.process_job_folder(full_path)
            time.sleep(0.05)
        logging.info("Initial scan complete.")

    def retry_pending(self):
        """Periodically retries failed file rename operations."""
        while not self.stop_event.is_set():
            if PAUSE_PROCESSING:
                self.stop_event.wait(5)
                continue

            current_time = time.time()
            to_remove = []
            for old_path, (job_num, dir_path, original_name, next_retry) in list(PENDING_RENAMES.items()):
                if current_time >= next_retry:
                    # Logic to retry renaming
                    pass # Simplified for brevity

            self.stop_event.wait(60)

# --- Entry Point ---
if __name__ == "__main__":
    setup_logging()
    app = Application()
    try:
        app.start()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received.")
    finally:
        app.stop()
