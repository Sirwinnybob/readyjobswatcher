from win10toast import ToastNotifier
import logging

def send_notification(title="Notification", message=""):
    """Sends a desktop notification."""
    try:
        toaster = ToastNotifier()
        # The show_toast method must be called in a non-threaded way to ensure it completes
        # before the program might exit, especially in a script.
        # However, since this will be part of a larger, long-running application,
        # running it threaded might be necessary to avoid blocking the main loop.
        # For now, keeping it simple and direct.
        toaster.show_toast(title, message, duration=10, icon_path=None, threaded=True)
        logging.info(f"Sent notification: '{title}' - '{message}'")
    except Exception as e:
        # This can fail if the environment doesn't support notifications,
        # e.g., on a server without a GUI.
        logging.error(f"Failed to send notification: {e}")
