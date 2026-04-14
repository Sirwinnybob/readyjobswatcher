import pytest
from unittest.mock import patch, MagicMock
from ready_jobs_watcher.notifications import send_notification

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
