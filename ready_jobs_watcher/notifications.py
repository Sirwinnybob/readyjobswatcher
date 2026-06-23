"""
Notifications Module.

Handles system-level desktop notifications for alerting the user to events
such as bad part detection or backup completion.
"""
from winotify import Notification
import logging
import os
import ctypes
import winsound

def send_notification(title: str = "Notification", message: str = "", duration: str = "short") -> None:
    """
    Sends a desktop notification to the user using winotify.

    This function will attempt to use an associated application icon if available.
    Errors arising from incompatible environments (like headless servers) are
    caught and logged, preventing application crashes.

    Args:
        title (str): The title text for the notification. Default is "Notification".
        message (str): The body text describing the event. Default is an empty string.
        duration (str): "short" or "long" display duration.
    """
    try:
        toast = Notification(
            app_id="Ready Jobs Watcher",
            title=title,
            msg=message,
            duration=duration  # short or long
        )

        # Try to set an icon if available
        icon_path = os.path.join(os.path.dirname(__file__), '..', 'favicon.ico')
        if os.path.exists(icon_path):
            toast.set_icon(icon_path)

        toast.show()
        logging.info(f"Sent notification: '{title}' - '{message}'")
    except Exception as e:
        # This can fail if the environment doesn't support notifications,
        # e.g., on a server without a GUI.
        logging.error(f"Failed to send notification: {e}")


def send_critical_alert(title: str, message: str) -> None:
    """
    Sends a major notification:
    - Long-duration Windows Toast notification
    - Windows Critical Stop error chime
    - Blocking, system-modal always-on-top MessageBox dialog
    """
    logging.critical(f"CRITICAL ALERT: {title} - {message}")

    # 1. Toast Notification
    send_notification(title, message, duration="long")

    # 2. Critical Stop Sound
    try:
        winsound.MessageBeep(winsound.MB_ICONHAND)
    except Exception as e:
        logging.error(f"Failed to play critical beep: {e}")

    # 3. Always-on-top blocking MessageBoxW dialog
    # MB_ICONERROR = 0x00000010
    # MB_SYSTEMMODAL = 0x00001000
    # MB_TOPMOST = 0x00040000
    # MB_SETFOREGROUND = 0x00010000
    try:
        flags = 0x00000010 | 0x00001000 | 0x00040000 | 0x00010000
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)
    except Exception as e:
        logging.error(f"Failed to show critical message box: {e}")
