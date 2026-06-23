import pytest
from unittest.mock import patch, MagicMock
from ready_jobs_watcher.notifications import send_notification, send_critical_alert

@patch("ready_jobs_watcher.notifications.Notification")
@patch("ready_jobs_watcher.notifications.os.path.exists")
@patch("ready_jobs_watcher.notifications.logging")
def test_send_notification_success(mock_logging, mock_exists, mock_notification):
    # Setup
    mock_exists.return_value = True
    mock_toast = MagicMock()
    mock_notification.return_value = mock_toast

    # Execute
    send_notification("Test Title", "Test Message")

    # Verify
    mock_notification.assert_called_once_with(
        app_id="Ready Jobs Watcher",
        title="Test Title",
        msg="Test Message",
        duration="short"
    )
    mock_toast.show.assert_called_once()
    mock_logging.info.assert_called_once_with("Sent notification: 'Test Title' - 'Test Message'")

@patch("ready_jobs_watcher.notifications.Notification")
@patch("ready_jobs_watcher.notifications.os.path.exists")
def test_send_notification_with_icon(mock_exists, mock_notification):
    # Setup
    mock_exists.return_value = True
    mock_toast = MagicMock()
    mock_notification.return_value = mock_toast

    # Execute
    send_notification("Title", "Msg")

    # Verify
    mock_toast.set_icon.assert_called_once()

@patch("ready_jobs_watcher.notifications.Notification")
@patch("ready_jobs_watcher.notifications.os.path.exists")
def test_send_notification_without_icon(mock_exists, mock_notification):
    # Setup
    mock_exists.return_value = False
    mock_toast = MagicMock()
    mock_notification.return_value = mock_toast

    # Execute
    send_notification("Title", "Msg")

    # Verify
    mock_toast.set_icon.assert_not_called()

@patch("ready_jobs_watcher.notifications.Notification")
@patch("ready_jobs_watcher.notifications.logging")
def test_send_notification_exception(mock_logging, mock_notification):
    # Setup
    mock_notification.side_effect = Exception("Test Error")

    # Execute
    send_notification("Title", "Msg")

    # Verify
    mock_logging.error.assert_called_once_with("Failed to send notification: Test Error")


@patch("ready_jobs_watcher.notifications.ctypes.windll.user32.MessageBoxW")
@patch("ready_jobs_watcher.notifications.winsound.MessageBeep")
@patch("ready_jobs_watcher.notifications.send_notification")
@patch("ready_jobs_watcher.notifications.logging")
def test_send_critical_alert(mock_logging, mock_send_notification, mock_message_beep, mock_message_box):
    # Execute
    send_critical_alert("Critical Title", "Critical Message")

    # Verify
    mock_logging.critical.assert_called_once_with("CRITICAL ALERT: Critical Title - Critical Message")
    mock_send_notification.assert_called_once_with("Critical Title", "Critical Message", duration="long")
    mock_message_beep.assert_called_once()
    mock_message_box.assert_called_once_with(
        0, "Critical Message", "Critical Title", 0x00000010 | 0x00001000 | 0x00040000 | 0x00010000
    )

