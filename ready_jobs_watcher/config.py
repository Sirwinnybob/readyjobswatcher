import os
import json
import datetime
import logging
import sys

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # When running as a PyInstaller executable from 'dist' folder
    BASE_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(sys.executable), '..'))
else:
    # When running as a script in development
    BASE_DATA_DIR = r'C:\Scripts\Ready Jobs Watcher'

main_logger = logging.getLogger('main')


class Config:
    def __init__(self):
        self.ROOT_DIR = r'Y:\Ready Jobs'
        self.CNC_SUBDIR = 'CNC'
        self.RETRY_INTERVAL_MINUTES = 15
        self.BACKUP_DIR = r'C:\Syncthing Backup'
        self.BACKUP_FOLDERS = [r'Y:\Ready Jobs', r'Y:\Upcoming Jobs']
        self.CONFIG_FILE = os.path.join(BASE_DATA_DIR, 'config.json')
        self.BACKUP_TIMES = ['00:00', '12:00']
        self.CNC_SCAN_TIMES = {
            'mon': '09:35',
            'tue': '09:35',
            'wed': '09:35',
            'thu': '09:35',
            'fri': '09:05',
            'sat': None, # No scan on Saturday
            'sun': None  # No scan on Sunday
        }
        # Planka configuration
        self.planka_board_identifier = "1529904146918934223"  # Can be ID or name
        self.planka_list_name = "CNC"  # Default list name
        self.load()

    def load(self):
        main_logger.debug(f"Loading config from {self.CONFIG_FILE}")
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.BACKUP_TIMES = config.get('backup_times', self.BACKUP_TIMES)
                    main_logger.info(f"Loaded backup times from config: {self.BACKUP_TIMES}")
            else:
                main_logger.debug(f"No config file found at {self.CONFIG_FILE}, using default backup times")
        except Exception as e:
            main_logger.error(f"Failed to load config: {e}")

    def save(self):
        main_logger.debug(f"Saving config to {self.CONFIG_FILE}")
        try:
            config = {'backup_times': self.BACKUP_TIMES}
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            main_logger.info(f"Saved backup times to config: {self.BACKUP_TIMES}")
        except Exception as e:
            main_logger.error(f"Failed to save config: {e}")

    def get_next_backup_time(self):
        logging.debug("Calculating next backup time")
        now = datetime.datetime.now()
        today = now.date()
        backup_datetimes = []
        for time_str in self.BACKUP_TIMES:
            try:
                hour, minute = map(int, time_str.split(':'))
                backup_time = datetime.datetime.combine(today, datetime.time(hour, minute))
                if backup_time <= now:
                    backup_time += datetime.timedelta(days=1)
                backup_datetimes.append(backup_time)
            except ValueError:
                logging.error(f"Invalid backup time format: {time_str}")
        return min(backup_datetimes) if backup_datetimes else now + datetime.timedelta(days=1)
