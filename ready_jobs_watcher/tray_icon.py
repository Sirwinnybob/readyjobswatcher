"""
System Tray Icon Module.

Manages the background system tray indicator and its context menu,
providing quick access to settings, manual tasks, and application controls
using PyQt6.
"""
import logging
import os
import sys
import subprocess
import threading
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor

from .config import BASE_DATA_DIR

def create_icon():
    """
    Creates or loads the icon for the system tray.

    Attempts to load 'favicon.ico'. If missing, generates a fallback.
    """
    logging.debug("Creating tray icon image")

    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    possible_paths = [
        os.path.join(base_path, 'favicon.ico'),
        os.path.join(BASE_DATA_DIR, 'favicon.ico'),
        os.path.join(os.path.dirname(__file__), '..', 'favicon.ico'),
    ]

    for icon_path in possible_paths:
        try:
            icon_path = os.path.abspath(icon_path)
            if os.path.exists(icon_path):
                logging.info(f"Loading tray icon from: {icon_path}")
                return QIcon(icon_path)
        except Exception as e:
            logging.debug(f"Failed to load icon from {icon_path}: {e}")
            continue

    logging.warning("Icon file not found. Using default generated icon.")
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(73, 109, 137))
    painter = QPainter(pixmap)
    painter.setPen(QColor(255, 255, 0))
    painter.drawText(10, 32, "RJW")
    painter.end()
    return QIcon(pixmap)


class RJWTrayIcon(QSystemTrayIcon):
    def __init__(self, settings_window, main_app, parent=None):
        super().__init__(create_icon(), parent)
        self.settings_window = settings_window
        self.main_app = main_app
        self.setToolTip("Ready Jobs Watcher")

        # Create Menu
        self.menu = QMenu()

        self.action_settings = self.menu.addAction("Open Settings")
        self.action_settings.triggered.connect(self.open_settings)

        self.action_backup = self.menu.addAction("Backup Now")
        self.action_backup.triggered.connect(self.backup_now)

        self.action_scan = self.menu.addAction("Scan CNC Now")
        self.action_scan.triggered.connect(self.scan_cnc_now)

        self.action_bad_parts = self.menu.addAction("View Bad Parts")
        self.action_bad_parts.triggered.connect(self.view_bad_parts)

        self.menu.addSeparator()

        self.action_restart = self.menu.addAction("Restart")
        self.action_restart.triggered.connect(self.restart_app)

        self.action_quit = self.menu.addAction("Quit")
        self.action_quit.triggered.connect(self.quit_app)

        self.setContextMenu(self.menu)

    def open_settings(self):
        logging.info("Open settings triggered from tray")
        self.settings_window.show_window()

    def backup_now(self):
        logging.info("Manual backup triggered from system tray.")
        # Trigger async if needed, or straight if thread-safe
        threading.Thread(target=self.main_app.perform_backup, daemon=True).start()

    def scan_cnc_now(self):
        logging.info("Manual CNC scan triggered from system tray.")
        threading.Thread(target=self.main_app.scan_cnc_pdfs_for_bad_parts, daemon=True).start()

    def view_bad_parts(self):
        logging.info("Open Bad Parts Center from tray.")
        self.settings_window.show_bad_parts_center()

    def restart_app(self):
        logging.info("Restarting application...")
        self.main_app.stop()
        self.hide()

        if hasattr(sys, '_MEIPASS'):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable] + sys.argv)

        QApplication.quit()
        sys.exit(0)

    def quit_app(self):
        logging.info("Shutting down...")
        self.main_app.stop()
        self.hide()
        QApplication.quit()
        sys.exit(0)

def create_tray_icon(settings_window, config, main_app):
    """
    Returns an instance of RJWTrayIcon.
    """
    return RJWTrayIcon(settings_window, main_app)
