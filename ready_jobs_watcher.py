import os
import time
import threading
from queue import Queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pystray
from PIL import Image, ImageDraw
import re
import logging
import shutil
import datetime
import tkinter as tk
from tkinter import messagebox
import ctypes
import stat
import json
from typing import Optional # Added import
import fitz # PyMuPDF
from tkinter import ttk
import sv_ttk
import winreg
from plyer import notification
import subprocess
import sys
import msvcrt # For file locking on Windows
import atexit
from plankapy import Planka, PasswordAuth, Board, List
from plankapy.routes import Routes
from plankapy import interfaces

# Custom compatible classes to handle API response incompatibilities with plankapy v2.2.2 and newer Planka servers
class CompatibleProject(interfaces.Project):
    def __init__(self, *args, **kwargs):
        # Known fields from plankapy v2.2.2
        known_fields = {'id', 'name', 'background', 'backgroundImage', 'position'}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in known_fields}
        super().__init__(*args, **filtered_kwargs)

    @property
    def boards(self) -> interfaces.QueryableList[interfaces.Board]:
        board_objects = []
        for board in self._included['boards']:
            # Known fields for Board
            known_fields = {'id', 'name', 'position', 'projectId'}
            board_filtered = {k: v for k, v in board.items() if k in known_fields}
            board_objects.append(CompatibleBoard(**board_filtered).bind(self.routes))
        return interfaces.QueryableList(board_objects)

class CompatibleBoard(interfaces.Board):
    def __init__(self, *args, **kwargs):
        # Known fields
        known_fields = {'id', 'name', 'position', 'projectId'}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in known_fields}
        super().__init__(*args, **filtered_kwargs)

    @property
    def lists(self) -> interfaces.QueryableList[interfaces.List]:
        list_objects = []
        for _list in self._included['lists']:
            # Known fields for List
            known_fields = {'id', 'name', 'position', 'boardId'}
            list_filtered = {k: v for k, v in _list.items() if k in known_fields}
            list_objects.append(CompatibleList(**list_filtered).bind(self.routes))
        return interfaces.QueryableList(list_objects)

class CompatibleList(interfaces.List):
    def create_card(self, *args, **kwargs):
        from plankapy.interfaces import Card
        overload = parse_overload(args, kwargs, model='card', options=('name', 'position', 'description', 'dueDate', 'isDueDateCompleted', 'stopwatch', 'creatorUserId', 'coverAttachmentId', 'isSubscribed'), required=('name',))
        overload['boardId'] = self.boardId
        overload['listId'] = self.id
        overload['position'] = overload.get('position', 0)
        # Fixed 'type' field for newer Planka API compatibility
        overload['type'] = 'project'
        route = self.routes.post_card(id=self.id)
        # Filter out extra fields from card creation response
        known_card_fields = {'id', 'name', 'position', 'description', 'dueDate', 'isDueDateCompleted', 'stopwatch', 'creatorUserId', 'listId', 'boardId', 'isSubscribed', 'coverAttachmentId'}
        card_response = route(**overload)['item']
        card_filtered = {k: v for k, v in card_response.items() if k in known_card_fields}
        return Card(**card_filtered).bind(self.routes)

# Custom Planka class to handle API compatibility issues
class CompatiblePlanka(Planka):
    @property
    def projects(self) -> interfaces.QueryableList[interfaces.Project]:
        route = self.routes.get_project_index()
        project_objects = []
        for project in route()['items']:
            # Filter to only known fields that plankapy v2.2.2 Project model supports
            known_fields = {'id', 'name', 'background', 'backgroundImage', 'position'}
            project_filtered = {k: v for k, v in project.items() if k in known_fields}
            project_objects.append(CompatibleProject(**project_filtered).bind(self.routes))
        return interfaces.QueryableList(project_objects)

# Import required function for compatible classes
def internal_parse_overload(args:tuple, kwargs: dict, model: str, options: tuple[str], required: tuple[str]=(), noarg=None) -> dict:
    """Internal copy of parse_overload for compatibility."""
    # Convert options and required to tuples if they have a single value
    if isinstance(options, str):
        options = (options,)
    if isinstance(required, str):
        required = (required,)

    # Unpack provided model
    if args and isinstance(args[0], interfaces.Model) or model in kwargs:
        return {**args[0]} if args else {**kwargs[model]}

    # Convert positional to keyword arguments
    elif args:
        coded_args = dict(zip(options, args))
        kwargs.update(coded_args)

    # Use self if no arguments are provided
    elif noarg and not kwargs:
        return {**noarg}

    # Check for required arguments
    if not all([arg in kwargs for arg in required]):
        raise ValueError(f'Required: {required}')

    return kwargs

# Use parse_overload (may not be exposed, so handle it)
try:
    from plankapy.interfaces import parse_overload
except ImportError:
    parse_overload = None
    # If not available, define it here, but since we have the function above, use it

# Since parse_overload might not be importable, use the internal version
def parse_overload(*args, **kwargs):
    return internal_parse_overload(*args, **kwargs)

# Planka integration constants
PLANKABAN_BASE_URL = "http://192.168.1.15:30064"
PLANKABAN_USERNAME = "bad_parts"
PLANKABAN_PASSWORD = "BadParts@KKC123"
# Board configuration - will be loaded from config during initialization
PLANKABAN_BOARD_IDENTIFIER = None  # Set during config loading
PLANKABAN_BOARD_ID = None  # Will be resolved at runtime
PLANKABAN_LIST_NAME = None  # Set during config loading

# --- Globals for Bad Part Logging ---
# The blacklisting system tracks PDFs with bad parts in two ways:
# - Temporary blacklist: Entries detected as bad, user can mark as 'complete' via manual log editing
# - Permanent ignore: Pages manually marked complete, never checked again
#
# Workflow: When a bad part is found, it's added to temporary blacklist and logged to Desktop text file.
# Users can append 'y' after 'COMPLETE:' in the log, which triggers removal from temp list and add to permanent ignore.

if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # When running as a PyInstaller executable from 'dist' folder
    BASE_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(sys.executable), '..'))
else:
    # When running as a script in development
    BASE_DATA_DIR = r'C:\Scripts\Ready Jobs Watcher'

BAD_PART_LOG_FILE = os.path.join(os.path.expanduser('~'), 'Desktop', 'Bad Parts Log.txt')
BLACKLIST_FILE = os.path.join(BASE_DATA_DIR, 'bad_parts_blacklist.json')
BLACKLISTED_FILES = set()  # Set of (pdf_path, page_num) tuples for temporary blacklist
PERMANENTLY_IGNORED_FILE = os.path.join(BASE_DATA_DIR, 'permanently_ignored_blacklist.json')
PERMANENTLY_IGNORED_FILES = set()  # Set of (pdf_path, page_num) tuples for permanent ignore
# ------------------------------------

class Config:
    def __init__(self):
        self.ROOT_DIR = r'Y:\Ready Jobs'
        self.CNC_SUBDIR = 'CNC'
        self.RETRY_INTERVAL_MINUTES = 15
        self.BACKUP_DIR = r'C:\Syncthing Backup'
        self.BACKUP_FOLDERS = [r'Y:\Ready Jobs', r'Y:\Upcoming Jobs']
        self.CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
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

# Global variables
PENDING_RENAMES = {}
LAST_BACKUP_TIME = None
PAUSE_PROCESSING = False
IS_PROCESSING_LOG_FILE = False
job_processor = None
stop_event = None

# Set up separate loggers for different components
def setup_logging():
    # Create formatters
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Main logger for general application activity
    main_logger = logging.getLogger('main')
    main_logger.setLevel(logging.DEBUG)
    main_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'ready_jobs_watcher.log'))
    main_handler.setFormatter(formatter)
    main_logger.addHandler(main_handler)
    
    # Backup logger
    backup_logger = logging.getLogger('backup')
    backup_logger.setLevel(logging.DEBUG)
    backup_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'backup.log'))
    backup_handler.setFormatter(formatter)
    backup_logger.addHandler(backup_handler)
    
    # CNC scan logger
    cnc_logger = logging.getLogger('cnc')
    cnc_logger.setLevel(logging.DEBUG)
    cnc_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'cnc_scan.log'))
    cnc_handler.setFormatter(formatter)
    cnc_logger.addHandler(cnc_handler)
    
    # Bad parts logger
    badparts_logger = logging.getLogger('badparts')
    badparts_logger.setLevel(logging.DEBUG)
    badparts_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'bad_parts.log'))
    badparts_handler.setFormatter(formatter)
    badparts_logger.addHandler(badparts_handler)

    # Planka logger
    planka_logger = logging.getLogger('planka')
    planka_logger.setLevel(logging.DEBUG)
    planka_handler = logging.FileHandler(os.path.join(BASE_DATA_DIR, 'planka.log'))
    planka_handler.setFormatter(formatter)
    planka_logger.addHandler(planka_handler)

    # Planka console handler
    planka_console = logging.StreamHandler()
    planka_console.setFormatter(formatter)
    planka_console.setLevel(logging.DEBUG)
    planka_logger.addHandler(planka_console)
    
    # Console handler (shared for all)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)  # Less verbose on console
    
    logging.getLogger('main').addHandler(console)
    logging.getLogger('backup').addHandler(console)
    logging.getLogger('cnc').addHandler(console)
    logging.getLogger('badparts').addHandler(console)
    
    return main_logger, backup_logger, cnc_logger, badparts_logger, planka_logger

main_logger, backup_logger, cnc_logger, badparts_logger, planka_logger = setup_logging()

def load_blacklist():
    """Loads the blacklist file into the global set for fast lookups."""
    global BLACKLISTED_FILES
    try:
        if os.path.exists(BLACKLIST_FILE) and os.path.getsize(BLACKLIST_FILE) > 0:
            with open(BLACKLIST_FILE, 'r') as f:
                # Load as list of lists, convert to set of tuples
                loaded_list = json.load(f)
                BLACKLISTED_FILES = set(tuple(item) for item in loaded_list)
                badparts_logger.info(f"Loaded {len(BLACKLISTED_FILES)} entries from blacklist.")
        else:
            BLACKLISTED_FILES = set()
            badparts_logger.info("Blacklist file is empty or does not exist. Initializing with empty blacklist.")
    except json.JSONDecodeError:
        badparts_logger.warning(f"Blacklist file '{BLACKLIST_FILE}' is malformed. Initializing with empty blacklist.")
        BLACKLISTED_FILES = set()
    except Exception as e:
        badparts_logger.error(f"Failed to load blacklist file: {e}")

def save_to_blacklist(pdf_path: str, page_num: int):
    """Adds a file and page number to the blacklist and saves it to the JSON file."""
    badparts_logger.debug(f"Attempting to add ({pdf_path}, {page_num}) to blacklist.")
    BLACKLISTED_FILES.add((pdf_path, page_num))
    try:
        with open(BLACKLIST_FILE, 'w') as f:
            # Convert set of tuples to list of lists for JSON serialization
            json.dump(list(list(item) for item in BLACKLISTED_FILES), f, indent=4)
        badparts_logger.info(f"Added {pdf_path} (page {page_num + 1}) to blacklist.")
        badparts_logger.debug(f"Current BLACKLISTED_FILES after add: {BLACKLISTED_FILES}")
    except Exception as e:
        badparts_logger.error(f"Failed to save blacklist file: {e}")

def check_for_bad_parts_highlight(pdf_path: str):
    """Checks a PDF for non-grayscale marks in the 'BAD PART(S)' area."""
    badparts_logger.debug(f"Current BLACKLISTED_FILES at start of check: {BLACKLISTED_FILES}")

    try:
        doc = fitz.open(pdf_path)
        for page_num, page in enumerate(doc):
            page_height = page.rect.height
            box_size = 22.5
            y_pos = page_height - 60
            x_bad_parts = 270
            bad_parts_rect = fitz.Rect(x_bad_parts - box_size/2, y_pos - box_size/2,
                                       x_bad_parts + box_size/2, y_pos + box_size/2)
            badparts_logger.debug(f"Page {page_num + 1} bad parts rect: {bad_parts_rect}")

            # NEW: Check if the current page is blacklisted before image analysis
            page_tuple = (pdf_path, page_num)
            is_page_blacklisted = page_tuple in BLACKLISTED_FILES
            badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is blacklisted: {is_page_blacklisted}")

            if is_page_blacklisted:
                badparts_logger.debug(f"Skipping blacklisted page {page_num + 1} of {pdf_path}.")
                continue # Skip to the next page

            # NEW: Check if the current page is permanently ignored
            is_page_permanently_ignored = page_tuple in PERMANENTLY_IGNORED_FILES
            badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is permanently ignored: {is_page_permanently_ignored}")

            if is_page_permanently_ignored:
                badparts_logger.info(f"Skipping permanently ignored page {page_num + 1} of {pdf_path}.")
                continue # Skip to the next page

            # Render page to pixmap
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # Crop to the bad parts rectangle
            crop_left = max(0, int(bad_parts_rect.x0))
            crop_upper = max(0, int(bad_parts_rect.y0))
            crop_right = min(img.width, int(bad_parts_rect.x1))
            crop_lower = min(img.height, int(bad_parts_rect.y1))

            cropped_img = img.crop((crop_left, crop_upper, crop_right, crop_lower))

            # Check for non-grayscale pixels
            is_bad_part = False
            tolerance = 1 # Allow for slight variations in RGB values for grayscale
            for x in range(cropped_img.width):
                for y in range(cropped_img.height):
                    r, g, b = cropped_img.getpixel((x, y))
                    # Check if not grayscale (R, G, B are not approximately equal)
                    if not (abs(r - g) <= tolerance and abs(r - b) <= tolerance and abs(g - b) <= tolerance):
                        is_bad_part = True
                        badparts_logger.debug(f"Non-grayscale pixel detected at ({x}, {y}) with RGB({r},{g},{b}) on page {page_num + 1}.") # NEW DEBUG LINE
                        break
                if is_bad_part:
                    break
            
            if is_bad_part:
                msg = f"BAD PART(S) marked on page {page_num + 1} of\n{os.path.basename(pdf_path)}"
                badparts_logger.warning(msg)

                log_entry = f"{os.path.basename(pdf_path)} | {pdf_path} | {page_num + 1} | Reported: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | COMPLETE: "
                with open(BAD_PART_LOG_FILE, 'a') as f:
                    f.write(log_entry + '\n')

                save_to_blacklist(pdf_path, page_num)

                # Create Planka card for the bad part
                badparts_logger.info(f"Attempting to create Planka card for bad part in {os.path.basename(pdf_path)} page {page_num + 1}")
                create_planka_card(pdf_path, page_num)

                # settings_window.root.after(0, lambda: messagebox.showwarning("Bad Part Alert", msg))
                # settings_window.root.after(0, lambda: messagebox.showwarning("Bad Part Alert", msg))
                # notification.notify(
                #     title="Bad Part Alert",
                #     message=msg,
                #     app_name="Ready Jobs Watcher",
                #     timeout=10
                # )
                try:
                    # Determine the path to the send_notification.py script
                    # In a PyInstaller onefile bundle, __file__ points to the temp directory
                    # where the extracted files are. sys._MEIPASS points to the root of that temp directory.
                    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                        # When frozen, send_notification.py is bundled in _MEIPASS
                        script_to_run = os.path.join(sys._MEIPASS, 'send_notification.py')
                    else:
                        # In development, send_notification.py is in the same directory
                        script_to_run = os.path.join(os.path.dirname(__file__), 'send_notification.py')

                    try:
                        # Attempt to use pythonw.exe first for a windowless execution
                        python_command = ['pythonw.exe', script_to_run, "Bad Part Alert", msg]
                        process = subprocess.Popen(python_command,
                                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        stdout, stderr = process.communicate(timeout=5)
                        if stdout:
                            badparts_logger.info(f"send_notification.py stdout: {stdout.decode().strip()}")
                        if stderr:
                            badparts_logger.error(f"send_notification.py stderr: {stderr.decode().strip()}")
                        badparts_logger.info("Notification subprocess launched with pythonw.exe.")
                    except FileNotFoundError:
                        badparts_logger.warning("pythonw.exe not found in PATH, trying python.exe.")
                        try:
                            # Fallback to python.exe if pythonw.exe is not found
                            python_command = ['python.exe', script_to_run, "Bad Part Alert", msg]
                            process = subprocess.Popen(python_command,
                                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            stdout, stderr = process.communicate(timeout=5)
                            if stdout:
                                badparts_logger.info(f"send_notification.py stdout: {stdout.decode().strip()}")
                            if stderr:
                                badparts_logger.error(f"send_notification.py stderr: {stderr.decode().strip()}")
                            badparts_logger.info("Notification subprocess launched with python.exe.")
                        except FileNotFoundError:
                            badparts_logger.error("Neither pythonw.exe nor python.exe found in PATH. Cannot send notification.")
                        except Exception as e:
                            badparts_logger.error(f"Failed to send notification via python.exe subprocess: {e}")
                    except Exception as e:
                        badparts_logger.error(f"Failed to send notification via pythonw.exe subprocess: {e}")
                except Exception as e:
                    badparts_logger.error(f"Failed to send notification via subprocess: {e}")
                
        doc.close()
    except Exception as e:
        badparts_logger.error(f"Failed to check PDF {pdf_path} for marks: {e}")

class RenameHandler(FileSystemEventHandler):
    def __init__(self, config, job_processor):
        super().__init__()
        self.config = config
        self.job_processor = job_processor
    def on_created(self, event):
        try:
            main_logger.debug(f"on_created triggered for {event.src_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring created event for {event.src_path}")
                return
            main_logger.info(f"Event detected: {event.src_path} (is_directory={event.is_directory})")
            if event.is_directory:
                self.job_processor.process_job_folder(event.src_path)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_created for {event.src_path}: {e}")

    def on_modified(self, event):
        try:
            main_logger.debug(f"on_modified triggered for {event.src_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring modified event for {event.src_path}")
                return
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_modified for {event.src_path}: {e}")

    def on_moved(self, event):
        try:
            main_logger.debug(f"on_moved triggered for {event.src_path} -> {event.dest_path}")
            if PAUSE_PROCESSING:
                main_logger.debug(f"Processing paused (GUI open): Ignoring moved event for {event.src_path} -> {event.dest_path}")
                return
            main_logger.info(f"Moved/renamed event detected: {event.src_path} -> {event.dest_path} (is_directory={event.is_directory})")
            if event.is_directory:
                self.job_processor.process_job_folder(event.dest_path, include_cnc=True)
        except Exception as e:
            main_logger.error(f"Error in RenameHandler.on_moved for {event.src_path} -> {event.dest_path}: {e}")

class PdfChangeHandler(FileSystemEventHandler):
    """Handles recursive modifications to PDF files for bad part checking."""
    def on_modified(self, event):
        try:
            if not event.is_directory and event.src_path.lower().endswith('.pdf'):
                main_logger.debug(f"PDF modified event detected by recursive watcher: {event.src_path}")
                check_for_bad_parts_highlight(event.src_path)
        except Exception as e:
            main_logger.error(f"Error in PdfChangeHandler.on_modified for {event.src_path}: {e}")

class LogFileHandler(FileSystemEventHandler):
    """
    Handles modifications to the bad parts log file on the desktop.
    When user marks a bad part as complete by appending 'y' after 'COMPLETE:'
    in a log line, this moves the entry from temporary blacklist to permanent ignore.
    Log format expected: "filename | full_path | page_num | Reported: timestamp | COMPLETE: "
    """
    def on_modified(self, event):
        global IS_PROCESSING_LOG_FILE
        if event.src_path == BAD_PART_LOG_FILE:
            if IS_PROCESSING_LOG_FILE:
                logging.debug("Skipping log file modification event (internal write).")
                return

            IS_PROCESSING_LOG_FILE = True
            try:
                logging.info(f"Change detected in {BAD_PART_LOG_FILE}. Processing...")
                time.sleep(0.5)
                
                with open(BAD_PART_LOG_FILE, 'r') as f:
                    lines = f.readlines()
                
                active_entries = []
                has_changes = False

                for line in lines:
                    parts = line.split('|')
                    # Expecting 5 parts now: filename | full_path | page_num | Reported: timestamp | COMPLETE:
                    if len(parts) == 5 and parts[4].strip().lower().startswith("complete: y"):
                        full_path = parts[1].strip()
                        page_num = int(parts[2].strip()) - 1 # Extract page number
                        
                        # Remove from blacklist
                        if (full_path, page_num) in BLACKLISTED_FILES:
                            BLACKLISTED_FILES.remove((full_path, page_num))
                            save_to_blacklist_internal() # Internal helper to save blacklist without re-adding
                            logging.info(f"Removed {full_path} (page {page_num}) from blacklist.")
                        
                        # Add to permanently ignored blacklist
                        PERMANENTLY_IGNORED_FILES.add((full_path, page_num))
                        save_permanently_ignored_blacklist_internal()
                        logging.info(f"Added {full_path} (page {page_num}) to permanently ignored blacklist.")
                        
                        logging.info(f"'{full_path}' (page {page_num}) marked as complete. Removing from log.")
                        has_changes = True
                    else:
                        active_entries.append(line)
                
                if has_changes:
                    with open(BAD_PART_LOG_FILE, 'w') as f:
                        f.writelines(active_entries)
                    logging.info("Bad parts log file updated.")

            except Exception as e:
                logging.error(f"Error processing log file: {e}")
            finally:
                IS_PROCESSING_LOG_FILE = False

def save_to_blacklist_internal():
    try:
        with open(BLACKLIST_FILE, 'w') as f:
            json.dump(list(list(item) for item in BLACKLISTED_FILES), f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save blacklist file internally: {e}")

def load_permanently_ignored_blacklist():
    """Loads the permanently ignored blacklist file into the global set for fast lookups."""
    global PERMANENTLY_IGNORED_FILES
    try:
        if os.path.exists(PERMANENTLY_IGNORED_FILE):
            with open(PERMANENTLY_IGNORED_FILE, 'r') as f:
                loaded_list = json.load(f)
                PERMANENTLY_IGNORED_FILES = set(tuple(item) for item in loaded_list)
                badparts_logger.info(f"Loaded {len(PERMANENTLY_IGNORED_FILES)} entries from permanently ignored blacklist.")
    except Exception as e:
        logging.error(f"Failed to load permanently ignored blacklist file: {e}")

def save_permanently_ignored_blacklist_internal():
    """Internal helper to save the permanently ignored blacklist without re-adding entries."""
    try:
        with open(PERMANENTLY_IGNORED_FILE, 'w') as f:
            json.dump(list(list(item) for item in PERMANENTLY_IGNORED_FILES), f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save permanently ignored blacklist file internally: {e}")

def resolve_board_id(planka):
    """Resolve the board identifier (ID or name) to a board ID."""
    global PLANKABAN_BOARD_ID

    # If already resolved as ID, return it
    if PLANKABAN_BOARD_ID and PLANKABAN_BOARD_ID != "unknown":
        return PLANKABAN_BOARD_ID

    try:
        routes = Routes(planka.handler)

        # If it's likely an ID (numeric), try direct lookup first
        if PLANKABAN_BOARD_IDENTIFIER and PLANKABAN_BOARD_IDENTIFIER.replace('-', '').replace('_', '').isalnum():
            try:
                planka_logger.debug(f"Attempting board lookup by ID: {PLANKABAN_BOARD_IDENTIFIER}")
                board_response = routes.get_board(id=PLANKABAN_BOARD_IDENTIFIER)()
                PLANKABAN_BOARD_ID = PLANKABAN_BOARD_IDENTIFIER
                planka_logger.debug(f"Board resolved by ID: {PLANKABAN_BOARD_ID}")
                return PLANKABAN_BOARD_ID
            except Exception as e:
                planka_logger.debug(f"Direct board ID lookup failed: {e}. Trying name lookup.")
                pass

        # Try to find board by name across all projects
        if PLANKABAN_BOARD_IDENTIFIER:
            try:
                planka_logger.debug(f"Attempting board lookup by name: {PLANKABAN_BOARD_IDENTIFIER}")

                # Get all projects and search through their boards
                projects = planka.projects
                for project in projects:
                    project_data = {k: v for k, v in project.__dict__.items() if k in ['id']}
                    proj = type('Project', (), project_data)()

                    try:
                        boards = proj.boards
                        for board in boards:
                            if board.name == PLANKABAN_BOARD_IDENTIFIER:
                                PLANKABAN_BOARD_ID = board.id
                                planka_logger.info(f"Board resolved by name '{PLANKABAN_BOARD_IDENTIFIER}' -> ID: {PLANKABAN_BOARD_ID}")
                                return PLANKABAN_BOARD_ID
                    except Exception as e:
                        planka_logger.debug(f"Error accessing boards for project {proj.id}: {e}")
                        continue

                planka_logger.warning(f"Board '{PLANKABAN_BOARD_IDENTIFIER}' not found by name lookup")

            except Exception as e:
                planka_logger.error(f"Board name lookup failed: {e}")

        PLANKABAN_BOARD_ID = "unknown"
        return None

    except Exception as e:
        planka_logger.error(f"Board resolution failed completely: {e}")
        PLANKABAN_BOARD_ID = "unknown"
        return None

def create_planka_card(pdf_path, page_num):
    """Creates individual Planka cards with checklists for each bad part found using compatible Planka API."""
    try:
        planka_logger.info("🔧 Starting Planka card creation for bad part detection")

        # Extract job number from PDF path: Y:\Ready Jobs\{job_number} - {description}\CNC\{filename}.pdf
        dir_path = os.path.dirname(pdf_path)
        job_dir = os.path.dirname(dir_path)  # Go up one level to get job folder
        job_folder_name = os.path.basename(job_dir)

        # Extract job number (e.g., "123-45" or "67")
        job_match = re.match(r"^(\d+-\d+|\d+[a-zA-Z]?)", job_folder_name)
        job_number = job_match.group(1) if job_match else "Unknown Job"

        # Extract job description (everything after " - ")
        job_description = ""
        if " - " in job_folder_name:
            job_description = job_folder_name.split(" - ", 1)[1]

        planka_logger.info(f"📋 Bad part detected - Job: {job_number}, Page: {page_num + 1}, File: {os.path.basename(pdf_path)}")

        try:
            # Initialize CompatiblePlanka with credentials
            planka = CompatiblePlanka(PLANKABAN_BASE_URL, PasswordAuth(PLANKABAN_USERNAME, PLANKABAN_PASSWORD))

            # Define target board and list
            PLANKABAN_BOARD_IDENTIFIER = config.planka_board_identifier  # e.g., "1529904146918934223"
            PLANKABAN_LIST_NAME = config.planka_list_name  # e.g., "CNC"

            # Navigate to projects, boards, lists - using compatible API
            projects = planka.projects
            if not projects:
                planka_logger.error("❌ No projects available in Planka")
                return

            # Use the first project (assuming user has access to at least one)
            project = projects[0]
            planka_logger.info(f"✅ Using project: {project.name}")

            # Get boards from project
            boards = project.boards
            planka_logger.debug(f"✅ Found {len(boards)} boards in project")

            # Find target board
            target_board = next((b for b in boards if b.id == PLANKABAN_BOARD_IDENTIFIER), None)
            if not target_board:
                planka_logger.error(f"❌ Target board {PLANKABAN_BOARD_IDENTIFIER} not found in project")
                return

            planka_logger.info(f"✅ Using board: {target_board.name} (ID: {target_board.id})")

            # Get lists from board
            lists = target_board.lists
            planka_logger.debug(f"✅ Found {len(lists)} lists in board")

            # Find target list
            cnc_list = next((l for l in lists if l.name == PLANKABAN_LIST_NAME), None)
            if not cnc_list:
                planka_logger.error(f"❌ '{PLANKABAN_LIST_NAME}' list not found in board")
                return

            planka_logger.info(f"✅ Using list: {cnc_list.name} (ID: {cnc_list.id})")

            # Create card name with limited description length for readability
            desc_limit = 30
            display_description = job_description[:desc_limit] + "..." if len(job_description) > desc_limit else job_description
            card_name = f"BAD PART: {job_number} - {display_description}"
            planka_logger.info(f"📝 Creating card: '{card_name}'")

            # Create the card using the compatible API
            new_card = cnc_list.create_card(name=card_name)
            planka_logger.info(f"✅ Card created successfully: '{new_card.name}' (ID: {new_card.id})")

            # Define the checklist tasks
            tasks_data = [
                f"Review drawing sheets - page {page_num + 1} in {os.path.basename(pdf_path)}",
                "Coordinate with manufacturing team",
                "Schedule rework time",
                "Verify parts availability",
                "Complete quality inspection after rework"
            ]

            # Add tasks to the card
            planka_logger.info("🛠️ Adding checklist tasks to card...")
            added_tasks = []
            tasks_success = True
            for i, task_name in enumerate(tasks_data):
                try:
                    task = new_card.add_task(name=task_name, position=i, isCompleted=False)
                    added_tasks.append(task)
                    planka_logger.info(f"   ✅ Task {i+1} added: '{task.name}' (ID: {task.id})")
                except Exception as e:
                    planka_logger.warning(f"   ⚠️ Task {i+1} failed: {e}")
                    tasks_success = False

            # Assign "AUTO ADDED" label to the card
            planka_logger.info(f"🏷️ Assigning 'AUTO ADDED' label to the card...")
            board_labels = target_board.labels
            planka_logger.debug(f"✅ Found {len(board_labels)} labels in board")

            # Look for existing "AUTO ADDED" label, or create it
            auto_added_label = next((l for l in board_labels if l.name == "AUTO ADDED"), None)

            if not auto_added_label:
                planka_logger.info("🔧 'AUTO ADDED' label not found, creating it...")
                try:
                    auto_added_label = target_board.create_label(name="AUTO ADDED", color="lime-green")
                    planka_logger.info("✅ Created 'AUTO ADDED' label")
                except Exception as e:
                    planka_logger.warning(f"⚠️ Failed to create 'AUTO ADDED' label: {e}")

            if auto_added_label:
                planka_logger.info(f"🔖 Using label: '{auto_added_label.name}' (color: {auto_added_label.color})")

                try:
                    card_label = new_card.add_label(auto_added_label)
                    planka_logger.info(f"✅ Label '{auto_added_label.name}' assigned to card: {card_label}")

                    # Refresh and verify label assignment
                    new_card.refresh()
                    planka_logger.info(f"🔍 Verified - Card now has {len(new_card.labels)} label(s)")

                except Exception as e:
                    planka_logger.warning(f"⚠️ Failed to assign label: {e}")

            planka_logger.info("🎉 Planka card with checklist and label created successfully!")

            # Log successful creation for monitoring
            success_log = f"PLANKAR CARD CREATED: {job_number} | {job_description} | Page {page_num + 1} | {os.path.basename(pdf_path)} | Card ID: {new_card.id} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            with open(BAD_PART_LOG_FILE, 'a') as f:
                f.write(success_log)

            # Success notification
            try:
                script_path = os.path.join(os.path.dirname(__file__), 'send_notification.py')
                notification_message = f"Bad Part Card Created: {job_number} - Planka card with checklist ready"

                import subprocess
                try:
                    subprocess.Popen(['pythonw.exe', script_path, "Bad Part Alert", notification_message],
                                   creationflags=subprocess.DETACHED_PROCESS,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except FileNotFoundError:
                    subprocess.Popen(['python.exe', script_path, "Bad Part Alert", notification_message],
                                   creationflags=subprocess.DETACHED_PROCESS,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                planka_logger.info("📢 Success notification sent")
            except Exception as e_notify:
                planka_logger.warning(f"Failed to send success notification: {e_notify}")

        except Exception as e_planka:
            planka_logger.error(f"❌ Failed to create Planka card: {e_planka}")

            # Fallback: create manual log entry if API fails
            log_entry = f"BAD PART DETECTED: {job_number} | {job_description} | Page {page_num + 1} | {os.path.basename(pdf_path)} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | MANUAL PLANKAR CARD NEEDED\n"
            log_entry += f"  💡 CREATE CARD: '{card_name}'\n"
            log_entry += f"  💡 ADD CHECKLIST:\n"
            for i, task in enumerate(tasks_data):
                log_entry += f"     - [ ] {task}\n"

            try:
                with open(BAD_PART_LOG_FILE, 'a') as f:
                    f.write(log_entry + "\n")
                planka_logger.info("✅ Bad part logged for manual Planka card creation")

                # Manual notification
                try:
                    script_path = os.path.join(os.path.dirname(__file__), 'send_notification.py')
                    notification_message = f"Bad Part Detected: {job_number} - Manual Planka card required"

                    import subprocess
                    try:
                        subprocess.Popen(['pythonw.exe', script_path, "Bad Part Alert", notification_message],
                                       creationflags=subprocess.DETACHED_PROCESS,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    except FileNotFoundError:
                        subprocess.Popen(['python.exe', script_path, "Bad Part Alert", notification_message],
                                       creationflags=subprocess.DETACHED_PROCESS,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    planka_logger.info("📢 Manual notification sent")
                except Exception as e_notify:
                    planka_logger.warning(f"Failed to send manual notification: {e_notify}")

            except Exception as e_log:
                planka_logger.error(f"Failed to write manual log: {e_log}")

    except Exception as e:
        planka_logger.error(f"❌ Critical error in bad parts processing: {e}")
        try:
            log_error = f"CRITICAL ERROR: Bad part processing failed - {str(e)}\n"
            with open(BAD_PART_LOG_FILE, 'a') as f:
                f.write(log_error)
        except Exception as e_log:
            planka_logger.error(f"Failed to log critical error: {e_log}")

def retry_pending(config, stop_event: threading.Event):
    while not stop_event.is_set():
        if PAUSE_PROCESSING:
            logging.debug("Retry paused (GUI open)")
            stop_event.wait(5)
            continue
        current_time = time.time()
        to_remove = []
        for old_path, (job_num, dir_path, original_name, next_retry) in list(PENDING_RENAMES.items()):
            if current_time >= next_retry:
                if not os.path.exists(old_path):
                    logging.info(f"Pending file no longer exists: {old_path}")
                    to_remove.append(old_path)
                    continue

                if ' - ' in original_name:
                    prefix, rest = original_name.split(' - ', 1)
                    new_name = job_num + ' - ' + rest
                else:
                    new_name = job_num + ' - ' + original_name
                new_path = os.path.join(dir_path, new_name)

                try:
                    os.rename(old_path, new_path)
                    logging.info(f"Retry successful: {old_path} -> {new_path}")
                    to_remove.append(old_path)
                except PermissionError:
                    logging.warning(f"Retry failed (locked): {old_path}. Rescheduling.")
                    PENDING_RENAMES[old_path] = (job_num, dir_path, original_name, current_time + (config.RETRY_INTERVAL_MINUTES * 60))
                except Exception as e:
                    logging.error(f"Retry error for {old_path}: {e}")
                    to_remove.append(old_path)

        for key in to_remove:
            del PENDING_RENAMES[key]

        stop_event.wait(60)

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
        # We check 'dirs' for 'codebase'
        if 'codebase' in dirs:
            folder_to_delete = os.path.join(root, 'codebase')
            try:
                shutil.rmtree(folder_to_delete)
                logging.info(f"Successfully deleted 'codebase' folder: {folder_to_delete}")
                # We can remove it from 'dirs' to prevent os.walk from trying to enter it
                dirs.remove('codebase')
            except Exception as e:
                logging.error(f"Failed to delete 'codebase' folder {folder_to_delete}: {e}")

def perform_backup(config):
    global LAST_BACKUP_TIME
    backup_logger.info("Starting backup...")
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M')
    for source in config.BACKUP_FOLDERS:
        # --- ADD THIS LINE ---
        delete_codebase_folders(source) # Clean up before backing up
        # ---------------------
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
    LAST_BACKUP_TIME = datetime.datetime.now()
    backup_logger.info("Backup complete.")
    if hasattr(settings_window, 'root') and settings_window.root.winfo_exists() and settings_window.root.winfo_viewable():
        settings_window.root.after(0, settings_window.update_status)

def scan_cnc_pdfs_for_bad_parts(config):
    cnc_logger.info("Starting scheduled scan of CNC PDFs for bad parts...")
    if PAUSE_PROCESSING:
        cnc_logger.info("Scheduled CNC PDF scan skipped (GUI open)")
        return

    # Reset bad parts log for this scan
    try:
        with open(BAD_PART_LOG_FILE, 'w') as f:
            f.write("")  # Clear the file
        badparts_logger.info("Bad parts log file reset for new scan.")
    except Exception as e:
        badparts_logger.error(f"Failed to reset bad parts log file: {e}")

    root_dir = config.ROOT_DIR
    cnc_subdir_name = config.CNC_SUBDIR

    for job_folder_name in os.listdir(root_dir):
        job_folder_path = os.path.join(root_dir, job_folder_name)
        if os.path.isdir(job_folder_path):
            # Check if it's a valid job folder (e.g., has a job number)
            if JobProcessor.is_job_folder(job_folder_path):
                cnc_folder_path = os.path.join(job_folder_path, cnc_subdir_name)
                if os.path.isdir(cnc_folder_path):
                    cnc_logger.debug(f"Scanning CNC folder: {cnc_folder_path}")
                    for item in os.listdir(cnc_folder_path):
                        item_path = os.path.join(cnc_folder_path, item)
                        if os.path.isfile(item_path) and item_path.lower().endswith('.pdf'):
                            cnc_logger.info(f"Checking PDF: {item_path}")
                            check_for_bad_parts_highlight(item_path)
                            time.sleep(0.05) # Introduce a small delay to limit resource usage
    cnc_logger.info("Scheduled CNC PDF scan complete.")

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
        perform_backup(config)

def cnc_scan_scheduler(config, stop_event: threading.Event):
    while not stop_event.is_set():
        now = datetime.datetime.now()
        today_weekday = now.strftime('%a').lower() # e.g., 'mon', 'tue'

        scan_time_str = config.CNC_SCAN_TIMES.get(today_weekday)

        if scan_time_str:
            try:
                scan_hour, scan_minute = map(int, scan_time_str.split(':'))
                scheduled_time_today = datetime.datetime.combine(now.date(), datetime.time(scan_hour, scan_minute))

                if now < scheduled_time_today:
                    next_scan_time = scheduled_time_today
                else:
                    # Already passed today's time, schedule for next week's same day
                    next_scan_time = scheduled_time_today + datetime.timedelta(days=7)

                cnc_logger.info(f"Next CNC scan scheduled for {next_scan_time}")
                sleep_seconds = (next_scan_time - now).total_seconds()
                if sleep_seconds > 0:
                    stop_event.wait(sleep_seconds)
                    if stop_event.is_set():
                        break
                    scan_cnc_pdfs_for_bad_parts(config)
                else:
                    # This case should ideally not be hit if scheduling is correct
                    cnc_logger.warning("CNC scan scheduler: sleep_seconds was not positive, scanning immediately.")
                    scan_cnc_pdfs_for_bad_parts(config)

            except ValueError:
                cnc_logger.error(f"Invalid CNC scan time format for {today_weekday}: {scan_time_str}")
                stop_event.wait(3600) # Wait an hour before retrying
            except Exception as e:
                cnc_logger.error(f"Error in CNC scan scheduler: {e}")
                stop_event.wait(3600) # Wait an hour before retrying
        else:
            cnc_logger.debug(f"No CNC scan scheduled for {today_weekday}. Waiting for next day.")
            # If no scan today, wait until tomorrow to re-evaluate
            tomorrow = now + datetime.timedelta(days=1)
            next_day_start = datetime.datetime.combine(tomorrow.date(), datetime.time(0, 0))
            sleep_seconds = (next_day_start - now).total_seconds()
            stop_event.wait(sleep_seconds + 60) # Add a minute to ensure we are past midnight

        if stop_event.is_set():
            break

    cnc_logger.info("CNC scan scheduler stopped.")

def initial_scan(config):
    logging.debug("Initial scan started")
    if PAUSE_PROCESSING:
        logging.info("Initial scan skipped (GUI open)")
        return
    logging.info("Starting initial scan...")
    for folder in os.listdir(config.ROOT_DIR):
        full_path = os.path.join(config.ROOT_DIR, folder)
        if is_hidden(full_path):
            logging.info(f"Skipping hidden item: {full_path}")
            continue
        job_processor.process_job_folder(full_path)
        time.sleep(0.05) # Introduce a small delay to limit resource usage
    logging.info("Initial scan complete.")

def manual_scan(config):
    logging.debug("Manual scan triggered")
    if PAUSE_PROCESSING:
        logging.info("Manual scan skipped (GUI open)")
        return
    initial_scan(config)

def create_image():
    logging.debug("Creating tray icon image")
    icon_path = os.path.join(BASE_DATA_DIR, 'favicon.ico')
    try:
        image = Image.open(icon_path)
        return image
    except FileNotFoundError:
        logging.error(f"Icon file not found at {icon_path}. Using default text image.")
        image = Image.new('RGB', (64, 64), color=(73, 109, 137))
        d = ImageDraw.Draw(image)
        d.text((10, 10), "RJW", fill=(255, 255, 0))
        return image

class SettingsWindow:
    def __init__(self, root, config):
        logging.debug("Initializing SettingsWindow")
        self.root = root
        self.config = config
        self.window = tk.Toplevel(root)
        self.window.title("Ready Jobs Watcher Settings")
        self.window.geometry("300x400")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

        # Apply modern styling
        self.window.configure(bg='#2b2b2b' if is_dark_mode() else '#ffffff')

        # Create main container with minimal padding
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Backup Status", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.last_backup_label = ttk.Label(main_frame, text="Last Backup: None", font=("Segoe UI", 10))
        self.last_backup_label.pack()
        self.next_backup_label = ttk.Label(main_frame, text="Next Backup: Calculating...", font=("Segoe UI", 10))
        self.next_backup_label.pack()

        ttk.Label(main_frame, text="Pending Replacements", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.pending_replacements_label = ttk.Label(main_frame, text="Count: 0", font=("Segoe UI", 10))
        self.pending_replacements_label.pack()

        ttk.Label(main_frame, text="Backup Times (HH:MM, 24-hour)", font=("Segoe UI", 10, "bold")).pack(pady=5)
        self.time1_entry = ttk.Entry(main_frame, width=10)
        self.time1_entry.insert(0, self.config.BACKUP_TIMES[0])
        self.time1_entry.pack()
        self.time2_entry = ttk.Entry(main_frame, width=10)
        self.time2_entry.insert(0, self.config.BACKUP_TIMES[1])
        self.time2_entry.pack()

        ttk.Button(main_frame, text="Save Schedule", command=self.save_schedule).pack(pady=5)
        ttk.Button(main_frame, text="Backup Now", command=lambda: threading.Thread(target=perform_backup, args=(self.config,), daemon=True).start()).pack(pady=5)
        ttk.Button(main_frame, text="Scan Ready Jobs Now", command=lambda: threading.Thread(target=manual_scan, args=(self.config,), daemon=True).start()).pack(pady=5)

        self.window.withdraw()
        self.window.update_idletasks()

    def show_window(self):
        global PAUSE_PROCESSING
        try:
            logging.debug("Showing GUI window")
            PAUSE_PROCESSING = True
            logging.info("GUI opened: Pausing file processing.")
            self.update_status()
            self.window.deiconify()
            self.window.update_idletasks()
            self.window.update()
            self.window.after(60000, self.update_status_periodic)
        except Exception as e:
            logging.error(f"Failed to open GUI: {e}")
            messagebox.showerror("Error", "Failed to open settings window.")

    def hide_window(self):
        global PAUSE_PROCESSING
        try:
            logging.debug("Hiding GUI window")
            PAUSE_PROCESSING = False
            logging.info("GUI closed: Resuming file processing.")
            self.window.withdraw()
            threading.Thread(target=manual_scan, args=(self.config,), daemon=True).start()
        except Exception as e:
            logging.error(f"Failed to close GUI: {e}")

    def update_status(self):
        global LAST_BACKUP_TIME
        try:
            logging.debug("Updating GUI status")
            if LAST_BACKUP_TIME:
                self.last_backup_label.config(text=f"Last Backup: {LAST_BACKUP_TIME.strftime('%Y-%m-%d %H:%M')}")
            else:
                self.last_backup_label.config(text="Last Backup: None")
            next_time = self.config.get_next_backup_time()
            self.next_backup_label.config(text=f"Next Backup: {next_time.strftime('%Y-%m-%d %H:%M')}")
            self.update_pending_replacements_count()
        except Exception as e:
            logging.error(f"Failed to update GUI status: {e}")

    def update_pending_replacements_count(self):
        global PENDING_RENAMES
        try:
            count = len(PENDING_RENAMES)
            self.pending_replacements_label.config(text=f"Count: {count}")
        except Exception as e:
            logging.error(f"Failed to update pending replacements count: {e}")

    def update_status_periodic(self):
        if self.window.winfo_viewable():
            self.update_status()
            self.update_pending_replacements_count()
            self.window.after(60000, self.update_status_periodic)

    def save_schedule(self):
        time1 = self.time1_entry.get()
        time2 = self.time2_entry.get()
        try:
            logging.debug(f"Saving schedule: {time1}, {time2}")
            for t in [time1, time2]:
                if t:
                    hour, minute = map(int, t.split(':'))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError
            new_times = [t for t in [time1, time2] if t]
            if not new_times:
                raise ValueError("At least one backup time must be specified.")
            self.config.BACKUP_TIMES = new_times
            self.config.save()
            logging.info(f"Backup schedule updated: {self.config.BACKUP_TIMES}")
            messagebox.showinfo("Success", "Backup schedule updated.")
            self.update_status()
        except ValueError:
            messagebox.showerror("Error", "Invalid time format. Use HH:MM (24-hour, e.g., 14:30).")
            logging.error(f"Failed to update backup schedule: Invalid format for {time1}, {time2}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save schedule: {e}")
            logging.error(f"Failed to save schedule: {e}")

def backup_now(icon, item, config):
    logging.debug("Backup now triggered from tray")
    logging.info("Manual backup triggered from system tray.")
    perform_backup(config)

def open_settings(icon, item):
    logging.debug("Open settings triggered from tray")
    settings_window.show_window()

def scan_cnc_now(icon, item, config):
    logging.debug("Scan CNC now triggered from tray")
    logging.info("Manual CNC scan triggered from system tray.")
    threading.Thread(target=scan_cnc_pdfs_for_bad_parts, args=(config,), daemon=True).start()

def quit_app(icon, item):
    logging.debug("Quit triggered from tray")
    logging.info("Shutting down...")
    stop_event.set()
    
    observer.stop()
    desktop_observer.stop()
    pdf_observer.stop()
    observer.join(timeout=1)
    desktop_observer.join(timeout=1)
    pdf_observer.join(timeout=1)

    retry_thread.join(timeout=1)
    backup_thread.join(timeout=1)
    cnc_scan_thread.join(timeout=1)
    try:
        settings_window.root.destroy()
    except Exception as e:
        logging.error(f"Failed to destroy GUI: {e}")
    icon.stop()

def cleanup_lock():
    global lock_file_handle
    if lock_file_handle:
        try:
            msvcrt.locking(lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1) # Release the lock
            lock_file_handle.close()
            logging.info("Released single instance lock.")
        except Exception as e:
            logging.error(f"Error releasing lock: {e}")

atexit.register(cleanup_lock)

def run_tkinter(config):
    logging.debug("Starting tkinter main loop")
    root = tk.Tk()
    root.withdraw()
    global settings_window

    # Apply modern theme
    theme = "dark" if is_dark_mode() else "light"
    sv_ttk.set_theme(theme)

    settings_window = SettingsWindow(root, config)
    root.after(100, lambda: initial_scan(config))
    root.mainloop()

# Detect system dark mode
def is_dark_mode():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except:
        return False

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

if __name__ == "__main__":

    config = Config()
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    os.makedirs(BASE_DATA_DIR, exist_ok=True)

    # Clear old logs if they are older than 7 days
    clear_old_logs()

    # --- Single Instance Lock ---
    LOCK_FILE = os.path.join(BASE_DATA_DIR, "ready_jobs_watcher.lock")
    lock_file_handle = None
    try:
        lock_file_handle = open(LOCK_FILE, 'w')
        msvcrt.locking(lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1) # Acquire non-blocking exclusive lock
        logging.info("Acquired single instance lock.")
    except IOError:
        logging.warning("Another instance is already running. Exiting.")
        sys.exit(0) # Exit if another instance is running
    # ----------------------------

    load_blacklist()

    logging.debug(f"BLACKLISTED_FILES after initial load: {BLACKLISTED_FILES}")

    load_permanently_ignored_blacklist()
    logging.debug(f"PERMANENTLY_IGNORED_FILES after initial load: {PERMANENTLY_IGNORED_FILES}")

    job_processor = JobProcessor(config)
    stop_event = threading.Event()

    retry_thread = threading.Thread(target=retry_pending, args=(config, stop_event), daemon=True)
    retry_thread.start()

    backup_thread = threading.Thread(target=backup_scheduler, args=(config, stop_event), daemon=True)
    backup_thread.start()

    # --- Initialize Planka constants from config ---
    PLANKABAN_BOARD_IDENTIFIER = config.planka_board_identifier
    PLANKABAN_LIST_NAME = config.planka_list_name
    main_logger.info(f"Planka board identifier: {PLANKABAN_BOARD_IDENTIFIER}, list: {PLANKABAN_LIST_NAME}")
    # -------------------------------------------------

    cnc_scan_thread = threading.Thread(target=cnc_scan_scheduler, args=(config, stop_event), daemon=True)
    cnc_scan_thread.start()

    # Watcher 1: Recursive Renaming
    event_handler = RenameHandler(config, job_processor)
    observer = Observer()
    observer.schedule(event_handler, config.ROOT_DIR, recursive=True)
    observer.start()
    logging.info(f"Watching {config.ROOT_DIR} recursively for folder changes...")

    # Watcher 2: Recursive PDF Changes
    pdf_event_handler = PdfChangeHandler()
    pdf_observer = Observer()
    pdf_observer.schedule(pdf_event_handler, config.ROOT_DIR, recursive=True)
    pdf_observer.start()
    logging.info(f"Watching {config.ROOT_DIR} recursively for PDF changes...")

    # Watcher 3: Desktop Log File
    desktop_path = os.path.dirname(BAD_PART_LOG_FILE)
    log_file_handler = LogFileHandler()
    desktop_observer = Observer()
    desktop_observer.schedule(log_file_handler, desktop_path, recursive=False)
    desktop_observer.start()
    logging.info(f"Watching {desktop_path} for log file changes...")

    icon = pystray.Icon('ready_jobs_watcher', create_image(), 'Ready Jobs Watcher')
    icon.menu = pystray.Menu(
        pystray.MenuItem('Open Settings', open_settings),
        pystray.MenuItem('Backup Now', lambda icon, item: backup_now(icon, item, config)),
        pystray.MenuItem('Scan CNC Now', lambda icon, item: scan_cnc_now(icon, item, config)),
        pystray.MenuItem('Quit', quit_app)
    )
    
    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()

    run_tkinter(config)
