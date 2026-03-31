"""
System Tray Icon Module.

Manages the background system tray indicator and its context menu,
providing quick access to settings, manual tasks, and application controls.
"""
import pystray
from PIL import Image, ImageDraw
import logging
import os
import sys
import subprocess
import threading

from .config import BASE_DATA_DIR

def create_image():
    """
    Creates or loads the image icon for the system tray.

    Attempts to load 'favicon.ico' from various probable locations depending
    on whether it's running from source or a PyInstaller bundle.
    If the icon is missing, falls back to generating a simple text-based image.

    Returns:
        PIL.Image: The image to be used for the tray icon.
    """
    logging.debug("Creating tray icon image")

    # Determine the base path - PyInstaller creates a temp folder and stores path in _MEIPASS
    if hasattr(sys, '_MEIPASS'):
        # Running in PyInstaller bundle
        base_path = sys._MEIPASS
        logging.debug(f"Running in PyInstaller bundle, base path: {base_path}")
    else:
        # Running in normal Python environment
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logging.debug(f"Running in development, base path: {base_path}")

    # Try multiple locations for the icon
    possible_paths = [
        os.path.join(base_path, 'favicon.ico'),  # PyInstaller bundle or project root
        os.path.join(BASE_DATA_DIR, 'favicon.ico'),  # Development fallback
        os.path.join(os.path.dirname(__file__), '..', 'favicon.ico'),  # Package root
    ]

    for icon_path in possible_paths:
        try:
            icon_path = os.path.abspath(icon_path)
            if os.path.exists(icon_path):
                logging.info(f"Loading tray icon from: {icon_path}")
                image = Image.open(icon_path)
                return image
        except Exception as e:
            logging.debug(f"Failed to load icon from {icon_path}: {e}")
            continue

    # Fallback to generated image
    logging.warning("Icon file not found in any location. Using default text image.")
    image = Image.new('RGB', (64, 64), color=(73, 109, 137))
    d = ImageDraw.Draw(image)
    d.text((10, 10), "RJW", fill=(255, 255, 0))
    return image

def backup_now(icon, item, app):
    """
    Action callback to trigger an immediate manual backup.

    Args:
        icon (pystray.Icon): The icon instance.
        item (pystray.MenuItem): The clicked menu item.
        app (Application): The main application instance.
    """
    logging.debug("Backup now triggered from tray")
    logging.info("Manual backup triggered from system tray.")
    app.perform_backup()

def open_settings(icon, item, settings_window):
    """
    Action callback to open the graphical settings window.

    Args:
        icon (pystray.Icon): The icon instance.
        item (pystray.MenuItem): The clicked menu item.
        settings_window (SettingsWindow): The GUI window instance.
    """
    logging.debug("Open settings triggered from tray")
    settings_window.show_window()

def scan_cnc_now(icon, item, app):
    """
    Action callback to trigger an immediate CNC scan in the background.

    Args:
        icon (pystray.Icon): The icon instance.
        item (pystray.MenuItem): The clicked menu item.
        app (Application): The main application instance.
    """
    logging.debug("Scan CNC now triggered from tray")
    logging.info("Manual CNC scan triggered from system tray.")
    threading.Thread(target=app.scan_cnc_pdfs_for_bad_parts, daemon=True).start()

def restart_app(icon, item, main_app):
    """
    Action callback to gracefully restart the application.

    Stops current threads, unmounts the tray icon, and spawns a new process
    mirroring the current execution context (script vs executable).

    Args:
        icon (pystray.Icon): The icon instance.
        item (pystray.MenuItem): The clicked menu item.
        main_app (Application): The main application instance.
    """
    logging.debug("Restart triggered from tray")
    logging.info("Restarting application...")

    # Stop the current instance
    main_app.stop()
    icon.stop()

    # Restart using the same executable or Python script
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller executable
        subprocess.Popen([sys.executable])
    else:
        # Running as Python script
        subprocess.Popen([sys.executable] + sys.argv)

    sys.exit(0)

def quit_app(icon, item, main_app):
    """
    Action callback to gracefully quit the application.

    Args:
        icon (pystray.Icon): The icon instance.
        item (pystray.MenuItem): The clicked menu item.
        main_app (Application): The main application instance.
    """
    logging.debug("Quit triggered from tray")
    logging.info("Shutting down...")
    main_app.stop()
    icon.stop()

def create_tray_icon(settings_window, config, main_app):
    """
    Constructs the system tray icon and attaches its context menu.

    Args:
        settings_window (SettingsWindow): The GUI settings window instance.
        config (Config): System configuration.
        main_app (Application): The main application instance.

    Returns:
        pystray.Icon: The configured system tray icon ready to be run.
    """
    icon = pystray.Icon('ready_jobs_watcher', create_image(), 'Ready Jobs Watcher')
    icon.menu = pystray.Menu(
        pystray.MenuItem('Open Settings', lambda: open_settings(icon, None, settings_window)),
        pystray.MenuItem('Backup Now', lambda: backup_now(icon, None, main_app)),
        pystray.MenuItem('Scan CNC Now', lambda: scan_cnc_now(icon, None, main_app)),
        pystray.MenuItem('Restart', lambda: restart_app(icon, None, main_app)),
        pystray.MenuItem('Quit', lambda: quit_app(icon, None, main_app))
    )
    return icon
