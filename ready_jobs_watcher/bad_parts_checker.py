import os
import json
import logging
import datetime
import sys
import subprocess
import fitz  # PyMuPDF
from PIL import Image

from .planka_api import create_planka_card
from .notifications import send_notification
from .config import BASE_DATA_DIR


badparts_logger = logging.getLogger('badparts')

# --- Globals for Bad Part Logging ---
BAD_PART_LOG_FILE = os.path.join(os.path.expanduser('~'), 'Desktop', 'Bad Parts Log.txt')
BLACKLIST_FILE = os.path.join(BASE_DATA_DIR, 'bad_parts_blacklist.json')
BLACKLISTED_FILES = set()  # Set of (pdf_path, page_num) tuples for temporary blacklist
PERMANENTLY_IGNORED_FILE = os.path.join(BASE_DATA_DIR, 'permanently_ignored_blacklist.json')
PERMANENTLY_IGNORED_FILES = set()  # Set of (pdf_path, page_num) tuples for permanent ignore
IS_PROCESSING_LOG_FILE = False


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


def check_for_bad_parts_highlight(pdf_path: str, config):
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

            page_tuple = (pdf_path, page_num)
            is_page_blacklisted = page_tuple in BLACKLISTED_FILES
            badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is blacklisted: {is_page_blacklisted}")

            if is_page_blacklisted:
                badparts_logger.debug(f"Skipping blacklisted page {page_num + 1} of {pdf_path}.")
                continue

            is_page_permanently_ignored = page_tuple in PERMANENTLY_IGNORED_FILES
            badparts_logger.debug(f"Checking page {page_num + 1} ({page_tuple}). Is permanently ignored: {is_page_permanently_ignored}")

            if is_page_permanently_ignored:
                badparts_logger.info(f"Skipping permanently ignored page {page_num + 1} of {pdf_path}.")
                continue

            pix = page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            crop_left = max(0, int(bad_parts_rect.x0))
            crop_upper = max(0, int(bad_parts_rect.y0))
            crop_right = min(img.width, int(bad_parts_rect.x1))
            crop_lower = min(img.height, int(bad_parts_rect.y1))

            cropped_img = img.crop((crop_left, crop_upper, crop_right, crop_lower))

            is_bad_part = False
            tolerance = 1
            for x in range(cropped_img.width):
                for y in range(cropped_img.height):
                    r, g, b = cropped_img.getpixel((x, y))
                    if not (abs(r - g) <= tolerance and abs(r - b) <= tolerance and abs(g - b) <= tolerance):
                        is_bad_part = True
                        badparts_logger.debug(f"Non-grayscale pixel detected at ({x}, {y}) with RGB({r},{g},{b}) on page {page_num + 1}.")
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

                create_planka_card(pdf_path, page_num, config)

                send_notification("Bad Part Alert", msg)

        doc.close()
    except Exception as e:
        badparts_logger.error(f"Failed to check PDF {pdf_path} for marks: {e}")

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
