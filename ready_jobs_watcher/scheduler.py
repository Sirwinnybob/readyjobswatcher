import logging
import datetime
import threading
import time
import shutil
import os

# These will be imported from other modules
# from .bad_parts_checker import scan_cnc_pdfs_for_bad_parts
from .utils import is_hidden, set_hidden_attribute, delete_codebase_folders
from .file_handler import JobProcessor

backup_logger = logging.getLogger('backup')
cnc_logger = logging.getLogger('cnc')

LAST_BACKUP_TIME = None

def perform_backup(config, app):
    backup_logger.info("Starting backup...")
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M')
    for source in config.BACKUP_FOLDERS:
        # delete_codebase_folders(source) # This will be fixed later
        backup_name = os.path.basename(source).replace(' ', '_') + '_' + timestamp
        backup_subdir = os.path.join(config.BACKUP_DIR, backup_name)
        try:
            shutil.copytree(source, backup_subdir, dirs_exist_ok=True, copy_function=shutil.copy2)
            backup_logger.info(f"Backed up {source} to {backup_subdir}")

            for root, dirs, _ in os.walk(backup_subdir):
                for dir_name in dirs:
                    backup_dir = os.path.join(root, dir_name)
                    source_dir = os.path.join(source, os.path.relpath(backup_dir, backup_subdir))
                    # if os.path.exists(source_dir) and is_hidden(source_dir):
                    #     set_hidden_attribute(backup_dir)
        except Exception as e:
            backup_logger.error(f"Backup failed for {source}: {e}")
    delete_old_backups(config)
    app.LAST_BACKUP_TIME = datetime.datetime.now()
    backup_logger.info("Backup complete.")
    if hasattr(app.settings_window, 'root') and app.settings_window.root.winfo_exists() and app.settings_window.root.winfo_viewable():
    #     settings_window.root.after(0, settings_window.update_status)

def delete_old_backups(config):
    logging.debug("Deleting old backups")
    threshold = datetime.datetime.now() - datetime.timedelta(days=7)
    for item in os.listdir(config.BACKUP_DIR):
        full_path = os.path.join(config.BACKUP_DIR, item)
        if os.path.isdir(full_path):
            parts = item.split('_')
            if len(parts) >= 4:
                try:
                    date_str = parts[-2] + '_' + parts[-1]
                    backup_date = datetime.datetime.strptime(date_str, '%Y-%m-%d_%H-%M')
                    if backup_date < threshold:
                        shutil.rmtree(full_path)
                        logging.info(f"Deleted old backup: {full_path}")
                except ValueError:
                    pass

def backup_scheduler(config, stop_event: threading.Event):
    while not stop_event.is_set():
        next_time = config.get_next_backup_time()
        sleep_seconds = (next_time - datetime.datetime.now()).total_seconds()
        if sleep_seconds > 0:
            backup_logger.info(f"Sleeping until next backup at {next_time}")
            stop_event.wait(sleep_seconds)
            if stop_event.is_set():
                break
        perform_backup(config, app)

def cnc_scan_scheduler(config, stop_event: threading.Event):
    while not stop_event.is_set():
        now = datetime.datetime.now()
        today_weekday = now.strftime('%a').lower()

        scan_time_str = config.CNC_SCAN_TIMES.get(today_weekday)

        if scan_time_str:
            try:
                scan_hour, scan_minute = map(int, scan_time_str.split(':'))
                scheduled_time_today = datetime.datetime.combine(now.date(), datetime.time(scan_hour, scan_minute))

                if now < scheduled_time_today:
                    next_scan_time = scheduled_time_today
                else:
                    next_scan_time = scheduled_time_today + datetime.timedelta(days=7)

                cnc_logger.info(f"Next CNC scan scheduled for {next_scan_time}")
                sleep_seconds = (next_scan_time - now).total_seconds()
                if sleep_seconds > 0:
                    stop_event.wait(sleep_seconds)
                    if stop_event.is_set():
                        break
                    scan_cnc_pdfs_for_bad_parts(config)
                else:
                    cnc_logger.warning("CNC scan scheduler: sleep_seconds was not positive, scanning immediately.")
                    scan_cnc_pdfs_for_bad_parts(config)

            except ValueError:
                cnc_logger.error(f"Invalid CNC scan time format for {today_weekday}: {scan_time_str}")
                stop_event.wait(3600)
            except Exception as e:
                cnc_logger.error(f"Error in CNC scan scheduler: {e}")
                stop_event.wait(3600)
        else:
            cnc_logger.debug(f"No CNC scan scheduled for {today_weekday}. Waiting for next day.")
            tomorrow = now + datetime.timedelta(days=1)
            next_day_start = datetime.datetime.combine(tomorrow.date(), datetime.time(0, 0))
            sleep_seconds = (next_day_start - now).total_seconds()
            stop_event.wait(sleep_seconds + 60)

        if stop_event.is_set():
            break

    cnc_logger.info("CNC scan scheduler stopped.")
