"""
Notifications Module.

Handles system-level desktop notifications for alerting the user to events
such as bad part detection or backup completion.
"""
from winotify import Notification
import logging
import os

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
