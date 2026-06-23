"""
Utility Functions Module.

Contains common helper functions used across the application,
including file system operations, log rotation, and system monitoring.
"""
import logging
import ctypes
import os
import shutil
import datetime
import re
import time

import fitz  # PyMuPDF

from .config import BASE_DATA_DIR

# Allowed PDF sheets for dark mode conversion
ALLOWED_SHEETS_PATTERN = re.compile(r'DELIVERY SHEET|ASSEMBLY SHEET|PLANS & ELEVATIONS', re.IGNORECASE)

_PDF_OPEN_RETRY_ATTEMPTS = 3
_PDF_OPEN_RETRY_DELAY_SECONDS = 0.3


def open_pdf_with_retry(pdf_path, attempts: int = _PDF_OPEN_RETRY_ATTEMPTS,
                         delay_seconds: float = _PDF_OPEN_RETRY_DELAY_SECONDS):
    """
    Open a PDF with fitz, retrying briefly on failure.

    Job-folder PDFs live on a network share and are sometimes still being
    written (briefly 0 bytes) or held open by another process when a
    filesystem event fires; a short retry rides out that window instead of
    treating a normal save as a parse failure.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fitz.open(pdf_path)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)

def is_hidden(folder_path):
    """
    Check if a file or folder has the Windows hidden attribute set.

    Args:
        folder_path (str): Path to the file or directory.

    Returns:
        bool: True if the hidden attribute is set, False otherwise.
    """
    logging.debug(f"Checking if hidden: {folder_path}")
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(folder_path)
        return attrs != -1 and (attrs & 0x2) != 0
    except OSError as e:
        logging.error(f"Failed to check hidden attribute for {folder_path}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error checking hidden attribute for {folder_path}: {e}", exc_info=True)
        return False

def set_hidden_attribute(folder_path):
    """
    Set the Windows hidden attribute on a file or folder.

    Args:
        folder_path (str): Path to the file or directory to hide.
    """
    logging.debug(f"Setting hidden attribute on {folder_path}")
    try:
        result = ctypes.windll.kernel32.SetFileAttributesW(folder_path, 0x2)
        if result:
            logging.info(f"Set hidden attribute on {folder_path}")
        else:
            logging.error(f"Failed to set hidden attribute on {folder_path}: Error code {ctypes.GetLastError()}")
    except OSError as e:
        logging.error(f"Failed to set hidden attribute on {folder_path}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error setting hidden attribute on {folder_path}: {e}", exc_info=True)

def delete_codebase_folders(directory_to_scan):
    """
    Walks through a directory and deletes any folder named 'codebase' using os.scandir for better performance.

    Args:
        directory_to_scan (str): The root directory to scan for 'codebase' folders.
    """
    logging.info(f"Scanning for 'codebase' folders to delete in {directory_to_scan}...")

    # Optimized using an iterative os.scandir approach instead of os.walk
    # This reduces overhead by avoiding full directory tree generation in memory
    stack = [directory_to_scan]

    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name == 'codebase':
                            folder_to_delete = entry.path
                            try:
                                shutil.rmtree(folder_to_delete)
                                logging.info(f"Successfully deleted 'codebase' folder: {folder_to_delete}")
                            except OSError as e:
                                logging.error(f"Failed to delete 'codebase' folder {folder_to_delete}: {e}")
                            except Exception as e:
                                logging.error(f"Unexpected error deleting 'codebase' folder {folder_to_delete}: {e}", exc_info=True)
                        else:
                            # Only add non-'codebase' directories to the stack for further scanning
                            stack.append(entry.path)
        except PermissionError:
            logging.warning(f"Permission denied accessing directory for codebase cleanup: {current_dir}")
        except OSError as e:
            logging.error(f"Error accessing directory for codebase cleanup {current_dir}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error scanning directory for codebase cleanup {current_dir}: {e}", exc_info=True)

def clear_old_logs():
    """
    Clear log files from previous days (keep only today's logs).

    Iterates over predefined log files and truncates any that were last modified before today.
    """
    log_files = [
        os.path.join(BASE_DATA_DIR, 'ready_jobs_watcher.log'),
        os.path.join(BASE_DATA_DIR, 'backup.log'),
        os.path.join(BASE_DATA_DIR, 'cnc_scan.log'),
        os.path.join(BASE_DATA_DIR, 'bad_parts.log'),
        os.path.join(BASE_DATA_DIR, 'send_notification.log')
    ]

    now = datetime.datetime.now()
    today_start = datetime.datetime.combine(now.date(), datetime.time(0, 0))

    for log_file in log_files:
        if os.path.exists(log_file):
            # Check if the file was last modified before today
            mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(log_file))

            if mod_time < today_start:
                # File is from a previous day - clear it
                try:
                    with open(log_file, 'w') as f:
                        f.truncate(0)
                    print(f"Cleared log file from previous day: {log_file} (last modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')})")
                except OSError as e:
                    print(f"Failed to clear log file {log_file}: {e}")
                except Exception as e:
                    import traceback
                    print(f"Unexpected error clearing log file {log_file}: {e}")
                    traceback.print_exc()

def cleanup_nested_dark_mode_folders(base_dir: str):
    """
    Clean up nested DARK MODE folders by flattening the structure.
    Moves PDFs from nested DARK MODE folders to the first-level DARK MODE folder.

    Args:
        base_dir (str): The base directory to scan (e.g., Y:\\Ready Jobs)
    """
    logging.info(f"Scanning for nested DARK MODE folders in {base_dir}...")
    folders_cleaned = 0
    files_moved = 0

    # Track nested dark mode folders for the second pass
    nested_dark_mode_folders = []

    # First pass: use iterative os.scandir for better performance than os.walk
    stack = [base_dir]

    while stack:
        current_dir = stack.pop()

        # Check if we're in a nested DARK MODE folder (more than one DARK MODE in path)
        path_parts = current_dir.split(os.sep)
        dark_mode_count = sum(1 for part in path_parts if part.upper() == "DARK MODE")

        is_nested = dark_mode_count > 1
        if is_nested:
            nested_dark_mode_folders.append(current_dir)
            # Find the first DARK MODE folder in the path
            first_dark_mode_idx = next(i for i, part in enumerate(path_parts) if part.upper() == "DARK MODE")
            correct_dark_mode_path = os.sep.join(path_parts[:first_dark_mode_idx + 1])

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file() and is_nested and entry.name.lower().endswith('.pdf'):
                        source = entry.path
                        dest = os.path.join(correct_dark_mode_path, entry.name)

                        try:
                            # Only move if destination doesn't exist or is older
                            if not os.path.exists(dest) or entry.stat().st_mtime > os.path.getmtime(dest):
                                shutil.move(source, dest)
                                logging.info(f"Moved {entry.name} from nested folder to {correct_dark_mode_path}")
                                files_moved += 1
                            else:
                                # Destination is newer, just delete the source
                                os.remove(source)
                                logging.info(f"Removed duplicate {entry.name} from nested folder")
                        except OSError as e:
                            logging.error(f"Failed to move {source} to {dest}: {e}")
                        except Exception as e:
                            logging.error(f"Unexpected error moving {source} to {dest}: {e}", exc_info=True)
        except PermissionError:
            logging.warning(f"Permission denied accessing directory for dark mode cleanup: {current_dir}")
        except OSError as e:
            logging.error(f"Error accessing directory for dark mode cleanup {current_dir}: {e}")

    # Second pass: remove empty nested DARK MODE folders
    # Process from deepest to shallowest to ensure empty parents can be deleted
    nested_dark_mode_folders.sort(key=lambda x: len(x.split(os.sep)), reverse=True)

    for folder_path in nested_dark_mode_folders:
        try:
            # Try to remove the directory (will only succeed if empty)
            os.rmdir(folder_path)
            logging.info(f"Removed empty nested DARK MODE folder: {folder_path}")
            folders_cleaned += 1
        except OSError:
            # Directory not empty or other error, skip it
            pass

    logging.info(f"Cleanup complete: {files_moved} files moved, {folders_cleaned} empty nested folders removed")

def log_system_stats():
    """
    Log system statistics including memory usage, thread count, and pending operations.
    Should be called periodically (e.g., hourly) to monitor application health.
    """
    try:
        import psutil
        import threading

        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024  # Convert to MB

        thread_count = threading.active_count()
        thread_names = [t.name for t in threading.enumerate()]

        logging.info(f"=== System Stats ===")
        logging.info(f"Memory usage: {memory_mb:.2f} MB")
        logging.info(f"Active threads: {thread_count}")
        logging.info(f"Thread names: {', '.join(thread_names)}")

        # Warn if memory usage is high (over 500MB)
        if memory_mb > 500:
            logging.warning(f"High memory usage detected: {memory_mb:.2f} MB")

        # Warn if too many threads (over 50)
        if thread_count > 50:
            logging.warning(f"High thread count detected: {thread_count}")

    except ImportError:
        logging.debug("psutil not available, skipping memory stats")
    except Exception as e:
        logging.error(f"Failed to log system stats: {e}", exc_info=True)
