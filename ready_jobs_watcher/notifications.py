from winotify import Notification
import logging
import os

def send_notification(title: str = "Notification", message: str = "") -> None:
    """Sends a desktop notification using winotify."""
    try:
        toast = Notification(
            app_id="Ready Jobs Watcher",
            title=title,
            msg=message,
            duration="short"  # short or long
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
