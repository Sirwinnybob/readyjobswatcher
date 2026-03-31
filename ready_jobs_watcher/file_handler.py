import os
import re
import logging
import time
from typing import Optional, Set

from .utils import is_hidden

# Files to ignore during processing (system files, temp files, etc.)
IGNORED_FILES: Set[str] = {
    'thumbs.db',
    'desktop.ini',
    '.ds_store',
    '~$',  # Office temp files prefix
}

# File extensions to ignore
IGNORED_EXTENSIONS: Set[str] = {
    '.tmp',
    '.temp',
    '.bak',
    '.swp',
}

# Folder names to ignore (template folders that should be renamed to job numbers)
IGNORED_FOLDER_NAMES: Set[str] = {
    'face frame',
    'frameless',
}


def should_ignore_file(filename: str) -> bool:
    """Check if a file should be ignored during processing."""
    lower_name = filename.lower()

    # Check exact matches
    if lower_name in IGNORED_FILES:
        return True

    # Check prefixes (like ~$ for Office temp files)
    for prefix in IGNORED_FILES:
        if prefix.startswith('~') and lower_name.startswith(prefix):
            return True

    # Check extensions
    _, ext = os.path.splitext(lower_name)
    if ext in IGNORED_EXTENSIONS:
        return True

    return False


def should_ignore_folder(folder_name: str) -> bool:
    """Check if a folder should be ignored (template folders waiting to be renamed)."""
    return folder_name.lower() in IGNORED_FOLDER_NAMES


class JobProcessor:
    def __init__(self, config, app_state):
        self.config = config
        self.app_state = app_state

    @staticmethod
    def extract_job_number(folder_name: str) -> Optional[str]:
        logging.debug(f"Extracting job number from {folder_name}")
        match = re.match(r"^(\d+-\d+|\d+[a-zA-Z]?)", folder_name)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def is_job_folder(folder_path: str) -> bool:
        folder_name = os.path.basename(folder_path)
        job_num = JobProcessor.extract_job_number(folder_name)
        logging.debug(f"Checking folder: {folder_path}, job_num: {job_num}")
        return job_num is not None

    def process_file(self, file_path: str, job_num: str, dir_path: str):
        logging.debug(f"Processing file {file_path}")
        if self.app_state.PAUSE_PROCESSING:
            logging.debug(f"Processing paused (GUI open): Skipping file {file_path}")
            return
        if not os.path.isfile(file_path):
            logging.warning(f"Not a file: {file_path}")
            return

        original_name = os.path.basename(file_path)

        # Skip ignored files (Thumbs.db, desktop.ini, temp files, etc.)
        if should_ignore_file(original_name):
            logging.debug(f"Ignoring system/temp file: {file_path}")
            return
        if ' - ' in original_name:
            prefix, rest = original_name.split(' - ', 1)
            if prefix == job_num:
                logging.info(f"Already correctly prefixed, skipping: {file_path}")
                return
            else:
                new_name = job_num + ' - ' + rest
        else:
            new_name = job_num + ' - ' + original_name

        new_path = os.path.join(dir_path, new_name)

        try:
            # Check if file still exists before attempting rename
            if not os.path.exists(file_path):
                logging.warning(f"File no longer exists, skipping: {file_path}")
                return

            # Check if destination already exists
            if os.path.exists(new_path):
                logging.info(f"Destination already exists, skipping: {new_path}")
                return

            os.rename(file_path, new_path)
            logging.info(f"Renamed: {file_path} -> {new_path}")
        except PermissionError:
            logging.warning(f"File locked: {file_path}. Scheduling retry.")
            with self.app_state.pending_renames_lock:
                self.app_state.PENDING_RENAMES[file_path] = (job_num, dir_path, original_name, time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60))
        except FileNotFoundError:
            logging.warning(f"File disappeared during rename attempt: {file_path}")
        except OSError as e:
            # Handle various OS errors (file in use, etc.)
            if hasattr(e, 'winerror') and e.winerror == 32:  # ERROR_SHARING_VIOLATION - file in use
                logging.warning(f"File in use (sharing violation): {file_path}. Scheduling retry.")
                with self.app_state.pending_renames_lock:
                    self.app_state.PENDING_RENAMES[file_path] = (job_num, dir_path, original_name, time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60))
            else:
                logging.error(f"OS error renaming {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error renaming {file_path}: {e}")

    def process_job_folder(self, job_folder: str, include_cnc: bool = False):
        logging.debug(f"Processing job folder {job_folder}, include_cnc={include_cnc}")
        if self.app_state.PAUSE_PROCESSING:
            logging.debug(f"Processing paused (GUI open): Skipping folder {job_folder}")
            return

        # Skip hidden folders
        if is_hidden(job_folder):
            logging.debug(f"Skipping hidden folder: {job_folder}")
            return

        folder_base_name = os.path.basename(job_folder)

        # Skip template folders (Face Frame, Frameless) until they're renamed to job numbers
        if should_ignore_folder(folder_base_name):
            logging.debug(f"Skipping template folder (waiting to be renamed): {job_folder}")
            return

        job_num = self.extract_job_number(folder_base_name)

        if not job_num:
            logging.warning(f"Could not extract job number from '{folder_base_name}'. Skipping folder.")
            return

        if not os.path.isdir(job_folder):
            logging.warning(f"Path is not a directory: {job_folder}. Skipping.")
            return

        try:
            with os.scandir(job_folder) as it:
                for entry in it:
                    # Skip hidden files
                    if is_hidden(entry.path):
                        logging.debug(f"Skipping hidden item: {entry.path}")
                        continue
                    if entry.is_file():
                        self.process_file(entry.path, job_num, job_folder)
        except PermissionError:
            logging.warning(f"Permission denied accessing folder: {job_folder}")
            return
        except OSError as e:
            logging.error(f"Error listing folder {job_folder}: {e}")
            return

        if include_cnc:
            cnc_path = os.path.join(job_folder, self.config.CNC_SUBDIR)
            if os.path.isdir(cnc_path) and not is_hidden(cnc_path):
                try:
                    with os.scandir(cnc_path) as it:
                        for entry in it:
                            if is_hidden(entry.path):
                                continue
                            if entry.is_file():
                                self.process_file(entry.path, job_num, cnc_path)
                except (PermissionError, OSError) as e:
                    logging.warning(f"Cannot access CNC folder {cnc_path}: {e}")
                    return
