import logging
import datetime
import threading
import time
import shutil
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .main import ReadyJobsWatcherApp

# These will be imported from other modules
# from .bad_parts_checker import scan_cnc_pdfs_for_bad_parts
from .utils import is_hidden, set_hidden_attribute, delete_codebase_folders, log_system_stats
from .file_handler import JobProcessor
from .config import Config
from .pdf_dark_mode import run_dark_mode_conversion

main_logger = logging.getLogger('main')

backup_logger = logging.getLogger('backup')
cnc_logger = logging.getLogger('cnc')

LAST_BACKUP_TIME = None

def perform_backup(config: Config, app: 'ReadyJobsWatcherApp') -> None:
    backup_logger.info("Starting backup...")

    # Run PDF dark mode conversion before backup
    backup_logger.info("Running PDF dark mode conversion before backup...")
    try:
        run_dark_mode_conversion(dry_run=False, theme="classic", invert_images=True)
    except Exception as e:
        backup_logger.error(f"PDF dark mode conversion failed, continuing with backup: {e}")

    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M')
    for source in config.BACKUP_FOLDERS:
        delete_codebase_folders(source)
        backup_name = os.path.basename(source).replace(' ', '_') + '_' + timestamp
        backup_subdir = os.path.join(config.BACKUP_DIR, backup_name)
        try:
            shutil.copytree(source, backup_subdir, dirs_exist_ok=True, copy_function=shutil.copy2)
            backup_logger.info(f"Backed up {source} to {backup_subdir}")

            for root, dirs, _ in os.walk(backup_subdir):
                for dir_name in dirs:
                    backup_dir = os.path.join(root, dir_name)
                    source_dir = os.path.join(source, os.path.relpath(backup_dir, backup_subdir))
                    if os.path.exists(source_dir) and is_hidden(source_dir):
                        set_hidden_attribute(backup_dir)
        except Exception as e:
            backup_logger.error(f"Backup failed for {source}: {e}")
    delete_old_backups(config)
    app.LAST_BACKUP_TIME = datetime.datetime.now()
    backup_logger.info("Backup complete.")
    if hasattr(app, 'settings_window') and app.settings_window and hasattr(app.settings_window, 'root') and app.settings_window.root.winfo_exists() and app.settings_window.root.winfo_viewable():
        app.settings_window.root.after(0, app.settings_window.update_status)

def delete_old_backups(config: Config) -> None:
    backup_logger.debug("Deleting old backups")
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
                        backup_logger.info(f"Deleted old backup: {full_path}")
                except ValueError:
                    pass

def scan_cnc_pdfs_for_bad_parts(config: Config) -> None:
    """Scans all CNC PDFs recursively for bad parts."""
    from .bad_parts_checker import check_for_bad_parts_highlight
    from .file_handler import JobProcessor

    cnc_logger.info("Starting CNC PDF scan for bad parts...")
    scanned_count = 0

    try:
        for folder in os.listdir(config.ROOT_DIR):
            full_path = os.path.join(config.ROOT_DIR, folder)

            if not os.path.isdir(full_path):
                continue

            if JobProcessor.is_job_folder(full_path):
                cnc_path = os.path.join(full_path, config.CNC_SUBDIR)
                if os.path.isdir(cnc_path):
                    for item in os.listdir(cnc_path):
                        if item.lower().endswith('.pdf'):
                            pdf_path = os.path.join(cnc_path, item)
                            try:
                                check_for_bad_parts_highlight(pdf_path, config)
                                scanned_count += 1
                            except Exception as e:
                                cnc_logger.error(f"Error scanning {pdf_path}: {e}")

        cnc_logger.info(f"CNC PDF scan complete. Scanned {scanned_count} PDFs.")
    except Exception as e:
        cnc_logger.error(f"Fatal error during CNC scan: {e}")

def backup_scheduler(config: Config, stop_event: threading.Event, app: 'ReadyJobsWatcherApp') -> None:
    while not stop_event.is_set():
        next_time = config.get_next_backup_time()
        sleep_seconds = (next_time - datetime.datetime.now()).total_seconds()
        if sleep_seconds > 0:
            backup_logger.info(f"Sleeping until next backup at {next_time}")
            stop_event.wait(sleep_seconds)
            if stop_event.is_set():
                break
        perform_backup(config, app)

def cnc_scan_scheduler(config: Config, stop_event: threading.Event) -> None:
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

def stats_logger_scheduler(stop_event: threading.Event) -> None:
    """Logs system statistics hourly for monitoring application health."""
    main_logger.info("Stats logger scheduler started")

    while not stop_event.is_set():
        # Wait for 1 hour
        if stop_event.wait(3600):  # 3600 seconds = 1 hour
            break

        # Log system stats
        try:
            log_system_stats()
        except Exception as e:
            main_logger.error(f"Error logging system stats: {e}")

    main_logger.info("Stats logger scheduler stopped")


def daily_restart_scheduler(config: Config, stop_event: threading.Event, app: 'ReadyJobsWatcherApp') -> None:
    """
    Scheduler that restarts the application daily at a configured time.
    This helps prevent memory leaks, clear stale state, and ensure stability.
    """
    import sys
    import subprocess

    main_logger.info(f"Daily restart scheduler started. Restart time: {config.daily_restart_time}")

    while not stop_event.is_set():
        try:
            now = datetime.datetime.now()

            # Parse the configured restart time
            try:
                restart_hour, restart_minute = map(int, config.daily_restart_time.split(':'))
            except (ValueError, AttributeError):
                main_logger.error(f"Invalid daily_restart_time format: {config.daily_restart_time}, using default 03:00")
                restart_hour, restart_minute = 3, 0

            # Calculate next restart time
            scheduled_time_today = datetime.datetime.combine(now.date(), datetime.time(restart_hour, restart_minute))

            if now >= scheduled_time_today:
                # Already past today's restart time, schedule for tomorrow
                next_restart = scheduled_time_today + datetime.timedelta(days=1)
            else:
                next_restart = scheduled_time_today

            sleep_seconds = (next_restart - now).total_seconds()
            main_logger.info(f"Next daily restart scheduled for {next_restart} (in {sleep_seconds/3600:.1f} hours)")

            # Wait until restart time
            if stop_event.wait(sleep_seconds):
                break

            # Time to restart
            main_logger.info("=== DAILY RESTART INITIATED ===")
            main_logger.info("Saving pending operations before restart...")

            # Log system stats before restart
            try:
                log_system_stats()
            except Exception as e:
                main_logger.error(f"Error logging stats before restart: {e}")

            # Give some time for pending operations to be saved
            time.sleep(2)

            main_logger.info("Restarting application...")

            # Get the executable path
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                executable = sys.executable
                args = [executable]
            else:
                # Running as script
                executable = sys.executable
                args = [executable] + sys.argv

            # Stop the current application cleanly
            try:
                app.stop()
            except Exception as e:
                main_logger.error(f"Error during clean shutdown: {e}")

            # Start a new instance
            try:
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

                main_logger.info("New instance started. Exiting current process.")
                os._exit(0)  # Force exit without cleanup (already done)

            except Exception as e:
                main_logger.error(f"Failed to restart application: {e}")
                # Continue running if restart fails
                continue

        except Exception as e:
            main_logger.error(f"Error in daily restart scheduler: {e}")
            # Wait an hour before trying again
            stop_event.wait(3600)

    main_logger.info("Daily restart scheduler stopped")
