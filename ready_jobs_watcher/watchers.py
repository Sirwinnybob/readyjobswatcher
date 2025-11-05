import logging
import time
from watchdog.events import FileSystemEventHandler

# This will be properly imported later
from .bad_parts_checker import check_for_bad_parts_highlight, IS_PROCESSING_LOG_FILE, BAD_PART_LOG_FILE, BLACKLISTED_FILES, PERMANENTLY_IGNORED_FILES, save_to_blacklist_internal, save_permanently_ignored_blacklist_internal


main_logger = logging.getLogger('main')

# This will be managed in main.py
PAUSE_PROCESSING = False

class RenameHandler(FileSystemEventHandler):
    def __init__(self, config, job_processor):
        super().__init__()
        self.config = config
        self.job_processor = job_processor
    def on_created(self, event):
        try:
            main_logger.debug(f"on_created triggered for {event.src_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring created event for {event.src_path}")
                return
            main_logger.info(f"Event detected: {event.src_path} (is_directory={event.is_directory})")
            if event.is_directory:
                self.job_processor.process_job_folder(event.src_path)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_created for {event.src_path}: {e}")

    def on_modified(self, event):
        try:
            main_logger.debug(f"on_modified triggered for {event.src_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring modified event for {event.src_path}")
                return
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_modified for {event.src_path}: {e}")

    def on_moved(self, event):
        try:
            main_logger.debug(f"on_moved triggered for {event.src_path} -> {event.dest_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring moved event for {event.src_path} -> {event.dest_path}")
                return
            main_logger.info(f"Moved/renamed event detected: {event.src_path} -> {event.dest_path} (is_directory={event.is_directory})")
            if event.is_directory:
                self.job_processor.process_job_folder(event.dest_path, include_cnc=True)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_moved for {event.src_path} -> {event.dest_path}: {e}")

class PdfChangeHandler(FileSystemEventHandler):
    def __init__(self, config):
        super().__init__()
        self.config = config
    """Handles recursive modifications to PDF files for bad part checking."""
    def on_modified(self, event):
        try:
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF modified event detected by recursive watcher: {event.src_path}")
                check_for_bad_parts_highlight(event.src_path, self.config)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_modified for {event.src_path}: {e}")

class LogFileHandler(FileSystemEventHandler):
    """
    Handles modifications to the bad parts log file on the desktop.
    When user marks a bad part as complete by appending 'y' after 'COMPLETE:'
    in a log line, this moves the entry from temporary blacklist to permanent ignore.
    Log format expected: "filename | full_path | page_num | Reported: timestamp | COMPLETE: "
    """
    def on_modified(self, event):
        global IS_PROCESSING_LOG_FILE
        if event.src_path == BAD_PART_LOG_FILE:
            if IS_PROCESSING_LOG_FILE:
                logging.debug("Skipping log file modification event (internal write).")
                return

            IS_PROCESSING_LOG_FILE = True
            try:
                logging.info(f"Change detected in {BAD_PART_LOG_FILE}. Processing...")
                time.sleep(0.5)

                with open(BAD_PART_LOG_FILE, 'r') as f:
                    lines = f.readlines()

                active_entries = []
                has_changes = False

                for line in lines:
                    parts = line.split('|')
                    # Expecting 5 parts now: filename | full_path | page_num | Reported: timestamp | COMPLETE:
                    if len(parts) == 5 and parts[4].strip().lower().startswith("complete: y"):
                        full_path = parts[1].strip()
                        page_num = int(parts[2].strip()) - 1 # Extract page number

                        # Remove from blacklist
                        if (full_path, page_num) in BLACKLISTED_FILES:
                            BLACKLISTED_FILES.remove((full_path, page_num))
                            save_to_blacklist_internal() # Internal helper to save blacklist without re-adding
                            logging.info(f"Removed {full_path} (page {page_num}) from blacklist.")

                        # Add to permanently ignored blacklist
                        PERMANENTLY_IGNORED_FILES.add((full_path, page_num))
                        save_permanently_ignored_blacklist_internal()
                        logging.info(f"Added {full_path} (page {page_num}) to permanently ignored blacklist.")

                        logging.info(f"'{full_path}' (page {page_num}) marked as complete. Removing from log.")
                        has_changes = True
                    else:
                        active_entries.append(line)

                if has_changes:
                    with open(BAD_PART_LOG_FILE, 'w') as f:
                        f.writelines(active_entries)
                    logging.info("Bad parts log file updated.")

            except Exception as e:
                logging.error(f"Error processing log file: {e}")
            finally:
                IS_PROCESSING_LOG_FILE = False
