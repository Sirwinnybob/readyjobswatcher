import os
import re
import logging
import time
from typing import Optional

# Globals that are used by the JobProcessor
PAUSE_PROCESSING = False
PENDING_RENAMES = {}

class JobProcessor:
    def __init__(self, config):
        self.config = config

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
        if PAUSE_PROCESSING:
            logging.debug(f"Processing paused (GUI open): Skipping file {file_path}")
            return
        if not os.path.isfile(file_path):
            logging.warning(f"Not a file: {file_path}")
            return

        original_name = os.path.basename(file_path)
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
            os.rename(file_path, new_path)
            logging.info(f"Renamed: {file_path} -> {new_path}")
        except PermissionError:
            logging.warning(f"File locked: {file_path}. Scheduling retry.")
            PENDING_RENAMES[file_path] = (job_num, dir_path, original_name, time.time() + (self.config.RETRY_INTERVAL_MINUTES * 60))
        except Exception as e:
            logging.error(f"Error renaming {file_path}: {e}")

    def process_job_folder(self, job_folder: str, include_cnc: bool = False):
        logging.debug(f"Processing job folder {job_folder}, include_cnc={include_cnc}")
        if PAUSE_PROCESSING:
            logging.debug(f"Processing paused (GUI open): Skipping folder {job_folder}")
            return

        folder_base_name = os.path.basename(job_folder)
        job_num = self.extract_job_number(folder_base_name)

        if not job_num:
            logging.warning(f"Could not extract job number from '{folder_base_name}'. Skipping folder.")
            return

        if not os.path.isdir(job_folder):
            logging.warning(f"Path is not a directory: {job_folder}. Skipping.")
            return

        for item in os.listdir(job_folder):
            full_path = os.path.join(job_folder, item)
            if os.path.isfile(full_path):
                self.process_file(full_path, job_num, job_folder)

        if include_cnc:
            cnc_path = os.path.join(job_folder, self.config.CNC_SUBDIR)
            if os.path.isdir(cnc_path):
                for item in os.listdir(cnc_path):
                    full_path = os.path.join(cnc_path, item)
                    if os.path.isfile(full_path):
                        self.process_file(full_path, job_num, cnc_path)
