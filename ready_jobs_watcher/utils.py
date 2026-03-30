import logging
import ctypes
import os
import shutil
import datetime

from .config import BASE_DATA_DIR

def is_hidden(folder_path):
    logging.debug(f"Checking if hidden: {folder_path}")
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(folder_path)
        return attrs != -1 and (attrs & 0x2) != 0
    except Exception as e:
        logging.error(f"Failed to check hidden attribute for {folder_path}: {e}")
        return False

def set_hidden_attribute(folder_path):
    logging.debug(f"Setting hidden attribute on {folder_path}")
    try:
        result = ctypes.windll.kernel32.SetFileAttributesW(folder_path, 0x2)
        if result:
            logging.info(f"Set hidden attribute on {folder_path}")
        else:
            logging.error(f"Failed to set hidden attribute on {folder_path}: Error code {ctypes.GetLastError()}")
    except Exception as e:
        logging.error(f"Failed to set hidden attribute on {folder_path}: {e}")

def delete_codebase_folders(directory_to_scan):
    """Walks through a directory and deletes any folder named 'codebase'."""
    logging.info(f"Scanning for 'codebase' folders to delete in {directory_to_scan}...")
    for root, dirs, files in os.walk(directory_to_scan, topdown=True):
        if 'codebase' in dirs:
            folder_to_delete = os.path.join(root, 'codebase')
            try:
                shutil.rmtree(folder_to_delete)
                logging.info(f"Successfully deleted 'codebase' folder: {folder_to_delete}")
                dirs.remove('codebase')
            except Exception as e:
                logging.error(f"Failed to delete 'codebase' folder {folder_to_delete}: {e}")

def clear_old_logs():
    """Clear log files from previous days (keep only today's logs)."""
    log_files = [
        os.path.join(BASE_DATA_DIR, 'ready_jobs_watcher.log'),
        os.path.join(BASE_DATA_DIR, 'backup.log'),
        os.path.join(BASE_DATA_DIR, 'cnc_scan.log'),
        os.path.join(BASE_DATA_DIR, 'bad_parts.log'),
        os.path.join(BASE_DATA_DIR, 'planka.log'),
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
                except Exception as e:
                    print(f"Failed to clear log file {log_file}: {e}")

def cleanup_nested_dark_mode_folders(base_dir: str):
    """
    Clean up nested DARK MODE folders by flattening the structure.
    Moves PDFs from nested DARK MODE folders to the first-level DARK MODE folder.

    Args:
        base_dir: The base directory to scan (e.g., Y:\\Ready Jobs)
    """
    logging.info(f"Scanning for nested DARK MODE folders in {base_dir}...")
    folders_cleaned = 0
    files_moved = 0

    for root, dirs, files in os.walk(base_dir):
        # Check if we're in a nested DARK MODE folder (more than one DARK MODE in path)
        path_parts = root.split(os.sep)
        dark_mode_count = sum(1 for part in path_parts if part.upper() == "DARK MODE")

        if dark_mode_count > 1:
            # Find the first DARK MODE folder in the path
            first_dark_mode_idx = next(i for i, part in enumerate(path_parts) if part.upper() == "DARK MODE")
            correct_dark_mode_path = os.sep.join(path_parts[:first_dark_mode_idx + 1])

            # Move all PDFs to the correct DARK MODE folder
            for file in files:
                if file.lower().endswith('.pdf'):
                    source = os.path.join(root, file)
                    dest = os.path.join(correct_dark_mode_path, file)

                    try:
                        # Only move if destination doesn't exist or is older
                        if not os.path.exists(dest) or os.path.getmtime(source) > os.path.getmtime(dest):
                            shutil.move(source, dest)
                            logging.info(f"Moved {file} from nested folder to {correct_dark_mode_path}")
                            files_moved += 1
                        else:
                            # Destination is newer, just delete the source
                            os.remove(source)
                            logging.info(f"Removed duplicate {file} from nested folder")
                    except Exception as e:
                        logging.error(f"Failed to move {source} to {dest}: {e}")

    # Second pass: remove empty nested DARK MODE folders
    for root, dirs, files in os.walk(base_dir, topdown=False):
        path_parts = root.split(os.sep)
        dark_mode_count = sum(1 for part in path_parts if part.upper() == "DARK MODE")

        if dark_mode_count > 1:
            try:
                # Try to remove the directory (will only succeed if empty)
                os.rmdir(root)
                logging.info(f"Removed empty nested DARK MODE folder: {root}")
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
        logging.error(f"Failed to log system stats: {e}")
