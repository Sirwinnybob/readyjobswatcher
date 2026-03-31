"""
Bad Parts Detection Module.

Provides functionality for scanning PDFs to identify quality control issues marked
by users in a specific "BAD PART(S)" bounding box. Manages blacklists to prevent
redundant processing.
"""
import os
import json
import logging
import datetime
import threading
from typing import Set, Tuple
import fitz  # PyMuPDF
from PIL import Image

from .notifications import send_notification
from .config import BASE_DATA_DIR, Config


badparts_logger = logging.getLogger('badparts')

# --- Constants for Bad Parts Detection ---
# These define the location and size of the "BAD PART(S)" checkbox on PDF pages
BAD_PARTS_BOX_SIZE = 22.5  # Size of the checkbox in points
BAD_PARTS_Y_OFFSET = 60  # Distance from bottom of page in points
BAD_PARTS_X_POSITION = 270  # X coordinate of checkbox center in points
COLOR_TOLERANCE = 1  # Maximum RGB difference to consider a pixel grayscale

# --- Globals for Bad Part Logging ---
BAD_PART_LOG_FILE = os.path.join(os.path.expanduser('~'), 'Desktop', 'Bad Parts Log.txt')
BLACKLIST_FILE = os.path.join(BASE_DATA_DIR, 'bad_parts_blacklist.json')
BLACKLISTED_FILES = set()  # Set of (pdf_path, page_num) tuples for temporary blacklist
PERMANENTLY_IGNORED_FILE = os.path.join(BASE_DATA_DIR, 'permanently_ignored_blacklist.json')
PERMANENTLY_IGNORED_FILES = set()  # Set of (pdf_path, page_num) tuples for permanent ignore

# Thread-safe locks for blacklist operations
BLACKLIST_LOCK = threading.Lock()
PERMANENTLY_IGNORED_LOCK = threading.Lock()
BAD_PART_LOG_LOCK = threading.Lock()  # Lock for writing to the bad parts log file


def load_blacklist() -> None:
    """
    Loads the temporary blacklist file into the global set for fast lookups.

    This function reads a JSON file representing a list of previously identified
    bad parts, converting it into a globally accessible set of tuples to avoid
    redundant processing on subsequent scans.
    """
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


def save_to_blacklist(pdf_path: str, page_num: int) -> None:
    """
    Adds a specific file and page number to the blacklist and saves it to the JSON file.

    Args:
        pdf_path (str): The full path to the PDF file.
        page_num (int): The 0-indexed page number containing the bad part.
    """
    badparts_logger.debug(f"Attempting to add ({pdf_path}, {page_num}) to blacklist.")
    with BLACKLIST_LOCK:
        BLACKLISTED_FILES.add((pdf_path, page_num))
        try:
            with open(BLACKLIST_FILE, 'w') as f:
                # Convert set of tuples to list of lists for JSON serialization
                json.dump(list(list(item) for item in BLACKLISTED_FILES), f, indent=4)
            badparts_logger.info(f"Added {pdf_path} (page {page_num + 1}) to blacklist.")
            badparts_logger.debug(f"Current BLACKLISTED_FILES after add: {BLACKLISTED_FILES}")
        except Exception as e:
            badparts_logger.error(f"Failed to save blacklist file: {e}")


def check_for_bad_parts_highlight(pdf_path: str, config: Config) -> None:
    """
    Checks a PDF document for non-grayscale marks in the designated 'BAD PART(S)' area.

    Iterates through all pages of the given PDF. For pages not already blacklisted
    or permanently ignored, it extracts a specific rectangular area and scans it
    for colored pixels indicating user markup. If found, it triggers notification,
    logging, and Planka card creation.

    Args:
        pdf_path (str): The path to the PDF to scan.
        config (Config): The application configuration, used for Planka integration.
    """
    badparts_logger.debug(f"Current BLACKLISTED_FILES at start of check: {BLACKLISTED_FILES}")

    doc = None
    try:
        # Open PDF document
        doc = fitz.open(pdf_path)

        # Iterate through all pages
        for page_num, page in enumerate(doc):
            try:
                page_height = page.rect.height
                y_pos = page_height - BAD_PARTS_Y_OFFSET
                bad_parts_rect = fitz.Rect(BAD_PARTS_X_POSITION - BAD_PARTS_BOX_SIZE/2, y_pos - BAD_PARTS_BOX_SIZE/2,
                                           BAD_PARTS_X_POSITION + BAD_PARTS_BOX_SIZE/2, y_pos + BAD_PARTS_BOX_SIZE/2)
                badparts_logger.debug(f"Page {page_num + 1} bad parts rect: {bad_parts_rect}")

                page_tuple = (pdf_path, page_num)

                # Thread-safe check of blacklist
                with BLACKLIST_LOCK:
                    is_page_blacklisted = page_tuple in BLACKLISTED_FILES
                badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is blacklisted: {is_page_blacklisted}")

                if is_page_blacklisted:
                    badparts_logger.debug(f"Skipping blacklisted page {page_num + 1} of {pdf_path}.")
                    continue

                # Thread-safe check of permanently ignored
                with PERMANENTLY_IGNORED_LOCK:
                    is_page_permanently_ignored = page_tuple in PERMANENTLY_IGNORED_FILES
                badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is permanently ignored: {is_page_permanently_ignored}")

                if is_page_permanently_ignored:
                    badparts_logger.info(f"Skipping permanently ignored page {page_num + 1} of {pdf_path}.")
                    continue

                pix = None
                img = None
                cropped_img = None
                try:
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                    crop_left = max(0, int(bad_parts_rect.x0))
                    crop_upper = max(0, int(bad_parts_rect.y0))
                    crop_right = min(img.width, int(bad_parts_rect.x1))
                    crop_lower = min(img.height, int(bad_parts_rect.y1))

                    cropped_img = img.crop((crop_left, crop_upper, crop_right, crop_lower))

                    is_bad_part = False
                    width = cropped_img.width

                    for i, (r, g, b) in enumerate(list(cropped_img.getdata())):
                        if not (abs(r - g) <= COLOR_TOLERANCE and abs(r - b) <= COLOR_TOLERANCE and abs(g - b) <= COLOR_TOLERANCE):
                            is_bad_part = True
                            x = i % width
                            y = i // width
                            badparts_logger.debug(f"Non-grayscale pixel detected at ({x}, {y}) with RGB({r},{g},{b}) on page {page_num + 1}.")
                            break

                    if is_bad_part:
                        msg = f"BAD PART(S) marked on page {page_num + 1} of\n{os.path.basename(pdf_path)}"
                        badparts_logger.warning(msg)

                        log_entry = f"{os.path.basename(pdf_path)} | {pdf_path} | {page_num + 1} | Reported: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | COMPLETE: "
                        # Thread-safe write to log file
                        with BAD_PART_LOG_LOCK:
                            with open(BAD_PART_LOG_FILE, 'a') as f:
                                f.write(log_entry + '\n')

                        save_to_blacklist(pdf_path, page_num)

                        # Import here to avoid circular import
                        from .planka_api import create_planka_card
                        create_planka_card(pdf_path, page_num, config)

                        send_notification("Bad Part Alert", msg)
                finally:
                    # Clean up resources to free memory
                    if cropped_img is not None:
                        cropped_img.close()
                        del cropped_img
                    if img is not None:
                        img.close()
                        del img
                    if pix is not None:
                        del pix

            except Exception as page_error:
                badparts_logger.error(f"Error processing page {page_num + 1} of {pdf_path}: {page_error}")
                # Continue to next page even if this one fails
                continue

    except FileNotFoundError:
        badparts_logger.warning(f"PDF file not found: {pdf_path}")
    except Exception as e:
        badparts_logger.error(f"Failed to check PDF {pdf_path} for marks: {e}", exc_info=True)
    finally:
        # Ensure document is always closed
        if doc is not None:
            try:
                doc.close()
                badparts_logger.debug(f"Closed PDF document: {pdf_path}")
            except Exception as close_error:
                badparts_logger.error(f"Error closing PDF {pdf_path}: {close_error}")

def save_to_blacklist_internal() -> None:
    """
    Internal helper to perform an atomic save of the blacklist.

    Assumes the caller already holds BLACKLIST_LOCK. Writes to a temporary
    file and then performs an atomic rename to prevent corruption in the event
    of an application crash or abrupt shutdown.
    """
    temp_file = None
    try:
        # Write to temp file first
        temp_file = BLACKLIST_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(list(list(item) for item in BLACKLISTED_FILES), f, indent=4)

        # Atomic rename (on Windows, need to remove target first)
        if os.path.exists(BLACKLIST_FILE):
            os.remove(BLACKLIST_FILE)
        os.rename(temp_file, BLACKLIST_FILE)

    except Exception as e:
        badparts_logger.error(f"Failed to save blacklist file internally: {e}", exc_info=True)
        # Clean up temp file on failure
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass

def load_permanently_ignored_blacklist() -> None:
    """
    Loads the permanently ignored blacklist file into the global set for fast lookups.

    This function reads a JSON file representing a list of bad parts that have
    been marked as resolved or ignored by the user.
    """
    global PERMANENTLY_IGNORED_FILES
    try:
        if os.path.exists(PERMANENTLY_IGNORED_FILE):
            with open(PERMANENTLY_IGNORED_FILE, 'r') as f:
                loaded_list = json.load(f)
                PERMANENTLY_IGNORED_FILES = set(tuple(item) for item in loaded_list)
                badparts_logger.info(f"Loaded {len(PERMANENTLY_IGNORED_FILES)} entries from permanently ignored blacklist.")
    except Exception as e:
        badparts_logger.error(f"Failed to load permanently ignored blacklist file: {e}")

def save_permanently_ignored_blacklist_internal() -> None:
    """
    Internal helper to perform an atomic save of the permanently ignored blacklist.

    Assumes the caller already holds PERMANENTLY_IGNORED_LOCK. Uses a temporary
    file to ensure atomicity.
    """
    temp_file = None
    try:
        # Write to temp file first
        temp_file = PERMANENTLY_IGNORED_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(list(list(item) for item in PERMANENTLY_IGNORED_FILES), f, indent=4)

        # Atomic rename (on Windows, need to remove target first)
        if os.path.exists(PERMANENTLY_IGNORED_FILE):
            os.remove(PERMANENTLY_IGNORED_FILE)
        os.rename(temp_file, PERMANENTLY_IGNORED_FILE)

    except Exception as e:
        badparts_logger.error(f"Failed to save permanently ignored blacklist file internally: {e}", exc_info=True)
        # Clean up temp file on failure
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
