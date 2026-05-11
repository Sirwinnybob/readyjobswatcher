"""
Configuration Management Module.

This module provides the `Config` class, which handles loading, validating,
and saving application configuration settings, including backup schedules
and processing delays.
"""
import os
import json
import datetime
import logging
import sys
from typing import Dict, List, Optional

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # When running as a PyInstaller executable from 'dist' folder
    # Go up two levels: from dist/ReadyJobsWatcher/ReadyJobsWatcher.exe to project root
    BASE_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(sys.executable), '..', '..'))
else:
    # When running as a script in development
    BASE_DATA_DIR = r'C:\Scripts\Ready Jobs Watcher'

main_logger = logging.getLogger('main')


class Config:
    """
    Manages application configuration settings.

    Responsible for persisting user settings to a local JSON file, restoring
    from backups if corrupted, and calculating future scheduled events.
    """
    def __init__(self):
        """Initializes default configuration settings and loads user overrides."""
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
        # PDF conversion and folder processing delays
        self.pdf_conversion_delay_seconds = 30  # Default: 30 seconds
        self.new_folder_delay_seconds = 1200  # Default: 20 minutes
        # Daily restart time (to prevent memory leaks and clear stale state)
        self.daily_restart_time = '03:00'  # Default: 3 AM
        # Bad-parts alerting mode and escalation settings
        self.bad_parts_mode = "tracker"  # tracker | legacy
        self.bad_parts_popup_enabled = True
        self.bad_parts_toast_enabled = True
        self.bad_parts_sound_profile = "triple_beep"  # triple_beep | none
        self.tracker_reconcile_interval_seconds = 300
        self.assimp_path: Optional[str] = None
        self.load()

    def _validate_config(self, config: Dict) -> bool:
        """
        Validate config structure and values.

        Args:
            config (Dict): The parsed JSON configuration dictionary.

        Returns:
            bool: True if valid, False otherwise.
        """
        # Check required fields exist and have correct types
        if 'backup_times' in config and not isinstance(config['backup_times'], list):
            main_logger.error("Config validation failed: backup_times must be a list")
            return False

        # Validate backup times format
        if 'backup_times' in config:
            for time_str in config['backup_times']:
                if not isinstance(time_str, str) or ':' not in time_str:
                    main_logger.error(f"Config validation failed: invalid backup time format: {time_str}")
                    return False
                try:
                    hour, minute = map(int, time_str.split(':'))
                    if not (0 <= hour < 24 and 0 <= minute < 60):
                        raise ValueError
                except ValueError:
                    main_logger.error(f"Config validation failed: invalid backup time: {time_str}")
                    return False

        # Validate delay values are positive integers
        if 'pdf_conversion_delay_seconds' in config:
            if not isinstance(config['pdf_conversion_delay_seconds'], (int, float)) or config['pdf_conversion_delay_seconds'] < 0:
                main_logger.error("Config validation failed: pdf_conversion_delay_seconds must be a positive number")
                return False

        if 'new_folder_delay_seconds' in config:
            if not isinstance(config['new_folder_delay_seconds'], (int, float)) or config['new_folder_delay_seconds'] < 0:
                main_logger.error("Config validation failed: new_folder_delay_seconds must be a positive number")
                return False

        if "bad_parts_mode" in config:
            if config["bad_parts_mode"] not in ("tracker", "legacy"):
                main_logger.error("Config validation failed: bad_parts_mode must be 'tracker' or 'legacy'")
                return False

        if "bad_parts_popup_enabled" in config and not isinstance(config["bad_parts_popup_enabled"], bool):
            main_logger.error("Config validation failed: bad_parts_popup_enabled must be a bool")
            return False

        if "bad_parts_toast_enabled" in config and not isinstance(config["bad_parts_toast_enabled"], bool):
            main_logger.error("Config validation failed: bad_parts_toast_enabled must be a bool")
            return False

        if "bad_parts_sound_profile" in config:
            if config["bad_parts_sound_profile"] not in ("triple_beep", "none"):
                main_logger.error("Config validation failed: bad_parts_sound_profile must be 'triple_beep' or 'none'")
                return False
        if "tracker_reconcile_interval_seconds" in config:
            value = config["tracker_reconcile_interval_seconds"]
            if not isinstance(value, (int, float)) or value < 30:
                main_logger.error(
                    "Config validation failed: tracker_reconcile_interval_seconds must be a number >= 30"
                )
                return False
        if "assimp_path" in config and config["assimp_path"] is not None:
            if not isinstance(config["assimp_path"], str):
                main_logger.error("Config validation failed: assimp_path must be a string or null")
                return False

        return True

    def load(self) -> None:
        """
        Loads the configuration from the JSON file.

        If the primary file is invalid or corrupted, it attempts to load from
        a backup configuration file.
        """
        main_logger.debug(f"Loading config from {self.CONFIG_FILE}")
        backup_file = self.CONFIG_FILE + '.backup'

        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r') as f:
                    config = json.load(f)

                # Validate config before using it
                if not self._validate_config(config):
                    # Try to restore from backup
                    if os.path.exists(backup_file):
                        main_logger.warning("Config file is invalid, attempting to restore from backup")
                        with open(backup_file, 'r') as f:
                            config = json.load(f)
                        if self._validate_config(config):
                            main_logger.info("Successfully restored config from backup")
                            # Save restored config
                            with open(self.CONFIG_FILE, 'w') as f:
                                json.dump(config, f, indent=4)
                        else:
                            main_logger.error("Backup config is also invalid, using defaults")
                            return
                    else:
                        main_logger.warning("No backup config available, using defaults")
                        return

                self.ROOT_DIR = config.get('root_dir', self.ROOT_DIR)
                self.CNC_SUBDIR = config.get('cnc_subdir', self.CNC_SUBDIR)
                self.BACKUP_DIR = config.get('backup_dir', self.BACKUP_DIR)
                self.BACKUP_FOLDERS = config.get('backup_folders', self.BACKUP_FOLDERS)
                self.BACKUP_TIMES = config.get('backup_times', self.BACKUP_TIMES)
                self.pdf_conversion_delay_seconds = config.get('pdf_conversion_delay_seconds', self.pdf_conversion_delay_seconds)
                self.new_folder_delay_seconds = config.get('new_folder_delay_seconds', self.new_folder_delay_seconds)
                self.daily_restart_time = config.get('daily_restart_time', self.daily_restart_time)
                self.bad_parts_mode = config.get("bad_parts_mode", self.bad_parts_mode)
                self.bad_parts_popup_enabled = config.get("bad_parts_popup_enabled", self.bad_parts_popup_enabled)
                self.bad_parts_toast_enabled = config.get("bad_parts_toast_enabled", self.bad_parts_toast_enabled)
                self.bad_parts_sound_profile = config.get("bad_parts_sound_profile", self.bad_parts_sound_profile)
                self.tracker_reconcile_interval_seconds = int(
                    config.get("tracker_reconcile_interval_seconds", self.tracker_reconcile_interval_seconds)
                )
                self.assimp_path = config.get("assimp_path", self.assimp_path)
                main_logger.info(f"Loaded backup times from config: {self.BACKUP_TIMES}")
            else:
                main_logger.debug(f"No config file found at {self.CONFIG_FILE}, using default backup times")
        except json.JSONDecodeError as e:
            main_logger.error(f"Config file is corrupted (invalid JSON): {e}")
            # Try to restore from backup
            if os.path.exists(backup_file):
                try:
                    main_logger.warning("Attempting to restore from backup")
                    with open(backup_file, 'r') as f:
                        config = json.load(f)
                    if self._validate_config(config):
                        self.ROOT_DIR = config.get('root_dir', self.ROOT_DIR)
                        self.CNC_SUBDIR = config.get('cnc_subdir', self.CNC_SUBDIR)
                        self.BACKUP_DIR = config.get('backup_dir', self.BACKUP_DIR)
                        self.BACKUP_FOLDERS = config.get('backup_folders', self.BACKUP_FOLDERS)
                        self.BACKUP_TIMES = config.get('backup_times', self.BACKUP_TIMES)
                        self.pdf_conversion_delay_seconds = config.get('pdf_conversion_delay_seconds', self.pdf_conversion_delay_seconds)
                        self.new_folder_delay_seconds = config.get('new_folder_delay_seconds', self.new_folder_delay_seconds)
                        self.daily_restart_time = config.get('daily_restart_time', self.daily_restart_time)
                        self.bad_parts_mode = config.get("bad_parts_mode", self.bad_parts_mode)
                        self.bad_parts_popup_enabled = config.get("bad_parts_popup_enabled", self.bad_parts_popup_enabled)
                        self.bad_parts_toast_enabled = config.get("bad_parts_toast_enabled", self.bad_parts_toast_enabled)
                        self.bad_parts_sound_profile = config.get("bad_parts_sound_profile", self.bad_parts_sound_profile)
                        self.tracker_reconcile_interval_seconds = int(
                            config.get("tracker_reconcile_interval_seconds", self.tracker_reconcile_interval_seconds)
                        )
                        self.assimp_path = config.get("assimp_path", self.assimp_path)
                        main_logger.info("Successfully restored config from backup")
                        # Save restored config
                        with open(self.CONFIG_FILE, 'w') as f:
                            json.dump(config, f, indent=4)
                    else:
                        main_logger.warning("Backup config validation failed, using defaults")
                except Exception as backup_error:
                    main_logger.error(f"Failed to restore from backup: {backup_error}, using defaults")
            else:
                main_logger.warning("No backup config available, using defaults")
        except Exception as e:
            main_logger.error(f"Failed to load config: {e}, using defaults")

    def save(self) -> None:
        """
        Saves the current configuration to the JSON file.

        Creates a `.backup` file of the existing configuration before overwriting.
        Validates the new configuration state prior to saving.
        """
        main_logger.debug(f"Saving config to {self.CONFIG_FILE}")
        backup_file = self.CONFIG_FILE + '.backup'

        try:
            config = {
                'root_dir': self.ROOT_DIR,
                'cnc_subdir': self.CNC_SUBDIR,
                'backup_dir': self.BACKUP_DIR,
                'backup_folders': self.BACKUP_FOLDERS,
                'backup_times': self.BACKUP_TIMES,
                'pdf_conversion_delay_seconds': self.pdf_conversion_delay_seconds,
                'new_folder_delay_seconds': self.new_folder_delay_seconds,
                'daily_restart_time': self.daily_restart_time,
                'bad_parts_mode': self.bad_parts_mode,
                'bad_parts_popup_enabled': self.bad_parts_popup_enabled,
                'bad_parts_toast_enabled': self.bad_parts_toast_enabled,
                'bad_parts_sound_profile': self.bad_parts_sound_profile,
                'tracker_reconcile_interval_seconds': self.tracker_reconcile_interval_seconds,
                'assimp_path': self.assimp_path
            }

            # Validate the config we're about to save
            if not self._validate_config(config):
                main_logger.error("Cannot save invalid config")
                return

            # Create backup of existing config before saving
            if os.path.exists(self.CONFIG_FILE):
                try:
                    import shutil
                    shutil.copy2(self.CONFIG_FILE, backup_file)
                    main_logger.debug(f"Created config backup at {backup_file}")
                except Exception as e:
                    main_logger.warning(f"Failed to create config backup: {e}")

            # Save new config
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            main_logger.info(f"Saved backup times to config: {self.BACKUP_TIMES}")
        except Exception as e:
            main_logger.error(f"Failed to save config: {e}")

    def get_next_backup_time(self) -> datetime.datetime:
        """
        Calculates the next scheduled backup time.

        Returns:
            datetime.datetime: The closest upcoming backup time.
        """
        main_logger.debug("Calculating next backup time")
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
                main_logger.error(f"Invalid backup time format: {time_str}")
        return min(backup_datetimes) if backup_datetimes else now + datetime.timedelta(days=1)
