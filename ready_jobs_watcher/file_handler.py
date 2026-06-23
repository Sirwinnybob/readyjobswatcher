"""
File Handling Module.

Provides functionality for processing job folders and standardizing
file names according to configured naming conventions.
"""
import os
import re
import logging
import time
import errno
from typing import Optional, Set

from .utils import is_hidden

# Files to ignore during processing (system files, temp files, etc.)
IGNORED_FILES: Set[str] = {
    'thumbs.db',
    'desktop.ini',
    '.ds_store',
    '~$',  # Office temp files prefix
}

# Pre-computed tuple of prefixes to check (faster than iterating)
IGNORED_PREFIXES = tuple(p for p in IGNORED_FILES if p.startswith('~'))

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

NEWPLUS_TEMPLATES_DIR = (
    os.environ.get("RJW_NEWPLUS_TEMPLATES_DIR")
    or r"C:\Users\chadc\AppData\Local\Microsoft\PowerToys\NewPlus\Templates"
)
_TEMPLATE_CACHE_SECONDS = 60.0
_template_folder_names_cache: Set[str] = set()
_template_folder_names_cache_at = 0.0
_NEW_FOLDER_PATTERN = re.compile(r"^new folder(?:\s*\(\d+\))?$", re.IGNORECASE)
RETRYABLE_OS_ERROR_WINERRORS = {
    32,    # ERROR_SHARING_VIOLATION
    33,    # ERROR_LOCK_VIOLATION
    53,    # ERROR_BAD_NETPATH
    59,    # ERROR_UNEXP_NET_ERR
    64,    # ERROR_NETNAME_DELETED
    67,    # ERROR_BAD_NET_NAME
    121,   # ERROR_SEM_TIMEOUT
    1231,  # ERROR_NETWORK_UNREACHABLE
}


def is_retryable_os_error(exc: BaseException) -> bool:
    """
    Return True when an OS-level error is likely transient and worth retrying.
    """
    if isinstance(exc, PermissionError):
        return True

    winerror = getattr(exc, "winerror", None)
    if isinstance(winerror, int) and winerror in RETRYABLE_OS_ERROR_WINERRORS:
        return True

    err_no = getattr(exc, "errno", None)
    if err_no in {
        errno.EACCES,
        errno.EBUSY,
        errno.EIO,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.ETIMEDOUT,
        errno.EHOSTUNREACH,
    }:
        return True

    message = str(exc).lower()
    transient_tokens = (
        "network name",
        "network path",
        "not reachable",
        "temporarily unavailable",
        "semaphore timeout",
        "sharing violation",
        "in use by another process",
        "device is not ready",
    )
    return any(token in message for token in transient_tokens)


def _normalize_folder_name(folder_name: str) -> str:
    return re.sub(r"\s+", " ", str(folder_name or "").strip()).lower()


def _load_newplus_template_folder_names() -> Set[str]:
    global _template_folder_names_cache
    global _template_folder_names_cache_at
    now = time.time()
    if (now - _template_folder_names_cache_at) < _TEMPLATE_CACHE_SECONDS:
        return set(_template_folder_names_cache)

    names: Set[str] = set()
    try:
        if os.path.isdir(NEWPLUS_TEMPLATES_DIR):
            for entry in os.scandir(NEWPLUS_TEMPLATES_DIR):
                if entry.is_dir():
                    normalized = _normalize_folder_name(entry.name)
                    if normalized:
                        names.add(normalized)
    except Exception as exc:
        logging.debug("Failed loading NewPlus template folder names: %s", exc)

    _template_folder_names_cache = names
    _template_folder_names_cache_at = now
    return set(_template_folder_names_cache)


def should_ignore_file(filename: str) -> bool:
    """
    Check if a file should be ignored during processing based on its name or extension.

    Args:
        filename (str): Name of the file to check.

    Returns:
        bool: True if the file should be skipped, False otherwise.
    """
    lower_name = filename.lower()

    # Check exact matches
    if lower_name in IGNORED_FILES:
        return True

    # Optimized prefix checking: use pre-computed tuple of prefixes
    if lower_name.startswith(IGNORED_PREFIXES):
        return True

    # Check extensions
    _, ext = os.path.splitext(lower_name)
    if ext in IGNORED_EXTENSIONS:
        return True

    return False


def should_ignore_folder(folder_name: str) -> bool:
    """
    Check if a folder should be ignored (e.g., unrenamed template folders).

    Args:
        folder_name (str): The folder name to verify.

    Returns:
        bool: True if the folder should be ignored, False otherwise.
    """
    normalized = _normalize_folder_name(folder_name)
    if normalized in IGNORED_FOLDER_NAMES:
        return True
    if _NEW_FOLDER_PATTERN.match(normalized):
        return True
    if normalized in _load_newplus_template_folder_names():
        return True
    return False


class JobProcessor:
    """
    Handles processing of job folders, including standardizing file names.
    """
    def __init__(self, config, app_state):
        """
        Initialize the JobProcessor.

        Args:
            config (Config): Configuration object containing operational settings.
            app_state (Application): Reference to the core application state.
        """
        self.config = config
        self.app_state = app_state

    @staticmethod
    def extract_job_number(folder_name: str) -> Optional[str]:
        """
        Extract the job number prefix from a folder name.

        Args:
            folder_name (str): The full folder name.

        Returns:
            Optional[str]: The extracted job number if matched, otherwise None.
        """
        logging.debug(f"Extracting job number from {folder_name}")
        match = re.match(r"^(\d+-\d+|\d+[a-zA-Z]?)", folder_name)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def is_job_folder(folder_path: str) -> bool:
        """
        Verify if a given path conforms to the expected job folder format.

        Args:
            folder_path (str): The full directory path.

        Returns:
            bool: True if the folder format matches job structure, False otherwise.
        """
        folder_name = os.path.basename(folder_path)
        job_num = JobProcessor.extract_job_number(folder_name)
        logging.debug(f"Checking folder: {folder_path}, job_num: {job_num}")
        return job_num is not None

    def process_file(self, file_path: str, job_num: str, dir_path: str):
        """
        Process an individual file, renaming it to include the job number prefix.

        If the file is locked or cannot be renamed, it schedules a retry.

        Args:
            file_path (str): Original full path of the file.
            job_num (str): Job number to prepend to the filename.
            dir_path (str): The directory containing the file.
        """
        logging.debug(f"Processing file {file_path}")
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
            if is_retryable_os_error(e):
                logging.warning(f"Transient OS error renaming {file_path}: {e}. Scheduling retry.")
                with self.app_state.pending_renames_lock:
                    self.app_state.PENDING_RENAMES[file_path] = (job_num, dir_path, original_name, time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60))
            else:
                logging.error(f"OS error renaming {file_path}: {e}")
        except Exception as e:
            logging.error(f"Error renaming {file_path}: {e}")

    def process_job_folder(self, job_folder: str, include_cnc: bool = False) -> bool:
        """
        Scan a job folder and process all applicable files within it.

        Args:
            job_folder (str): Full path to the job folder directory.
            include_cnc (bool): Whether to recursively process the CNC subdirectory.
        """
        logging.debug(f"Processing job folder {job_folder}, include_cnc={include_cnc}")

        # Skip hidden folders
        if is_hidden(job_folder):
            logging.debug(f"Skipping hidden folder: {job_folder}")
            return True

        folder_base_name = os.path.basename(job_folder)

        # Skip template folders (Face Frame, Frameless) until they're renamed to job numbers
        if should_ignore_folder(folder_base_name):
            logging.debug(f"Skipping template folder (waiting to be renamed): {job_folder}")
            return True

        job_num = self.extract_job_number(folder_base_name)

        if not job_num:
            logging.warning(f"Could not extract job number from '{folder_base_name}'. Skipping folder.")
            return True

        if not os.path.isdir(job_folder):
            logging.warning(f"Path is not a directory: {job_folder}. Skipping.")
            return True

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
            return False
        except OSError as e:
            logging.error(f"Error listing folder {job_folder}: {e}")
            return not is_retryable_os_error(e)

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
                    if isinstance(e, PermissionError):
                        return False
                    return not is_retryable_os_error(e)
        return True
