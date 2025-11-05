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
    """Clear log files older than 7 days to prevent accumulation."""
    log_files = [
        os.path.join(BASE_DATA_DIR, 'ready_jobs_watcher.log'),
        os.path.join(BASE_DATA_DIR, 'backup.log'),
        os.path.join(BASE_DATA_DIR, 'cnc_scan.log'),
        os.path.join(BASE_DATA_DIR, 'bad_parts.log'),
        os.path.join(BASE_DATA_DIR, 'planka.log')
    ]
    now = datetime.datetime.now()
    for log_file in log_files:
        if os.path.exists(log_file):
            mod_time = os.path.getmtime(log_file)
            file_age = now - datetime.datetime.fromtimestamp(mod_time)
            if file_age.days >= 7:
                try:
                    with open(log_file, 'w') as f:
                        f.truncate(0)
                    print(f"Cleared old log file: {log_file} (age: {file_age.days} days)")
                except Exception as e:
                    print(f"Failed to clear log file {log_file}: {e}")
