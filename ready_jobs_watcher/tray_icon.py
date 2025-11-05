import pystray
from PIL import Image, ImageDraw
import logging
import os

# These will be imported from other modules
# from . import main
# from .scheduler import perform_backup
# from .bad_parts_checker import scan_cnc_pdfs_for_bad_parts
from .config import BASE_DATA_DIR

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

def backup_now(icon, item, app):
    logging.debug("Backup now triggered from tray")
    logging.info("Manual backup triggered from system tray.")
    app.perform_backup()

def open_settings(icon, item, settings_window):
    logging.debug("Open settings triggered from tray")
    settings_window.show_window()

def scan_cnc_now(icon, item, app):
    logging.debug("Scan CNC now triggered from tray")
    logging.info("Manual CNC scan triggered from system tray.")
    threading.Thread(target=app.scan_cnc_pdfs_for_bad_parts, daemon=True).start()

def quit_app(icon, item, main_app):
    logging.debug("Quit triggered from tray")
    logging.info("Shutting down...")
    main_app.stop()
    icon.stop()

def create_tray_icon(settings_window, config, main_app):
    icon = pystray.Icon('ready_jobs_watcher', create_image(), 'Ready Jobs Watcher')
    icon.menu = pystray.Menu(
        pystray.MenuItem('Open Settings', lambda: open_settings(icon, None, settings_window)),
        pystray.MenuItem('Backup Now', lambda: backup_now(icon, None, main_app)),
        pystray.MenuItem('Scan CNC Now', lambda: scan_cnc_now(icon, None, main_app)),
        pystray.MenuItem('Quit', lambda: quit_app(icon, None, main_app))
    )
    return icon
