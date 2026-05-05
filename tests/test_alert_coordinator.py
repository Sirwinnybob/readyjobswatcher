import time
from unittest.mock import MagicMock, patch

from ready_jobs_watcher.alert_coordinator import AlertBatch, AlertCoordinator
from ready_jobs_watcher.config import Config
from ready_jobs_watcher.tracker_bad_parts import TrackerBadPartEvent, TrackerBadPartKey


def _sample_event():
    return TrackerBadPartEvent(
        key=TrackerBadPartKey(
            job_folder_name="100 - TEST",
            pdf_filename="100 - Maple.pdf",
            page=2,
            file_fingerprint="fp-1",
            part_number=9,
        ),
        material_or_pdf="Maple",
        detected_at="2026-05-04T10:00:00Z",
    )


def test_acknowledge_batch_calls_monitor():
    cfg = Config()
    monitor = MagicMock()
    monitor.acknowledge_keys.return_value = 1
    coordinator = AlertCoordinator(cfg, monitor, popup_notifier=None)
    event = _sample_event()
    alert_batch = AlertBatch(events=[event])
    assert coordinator.acknowledge_batch(alert_batch) == 1
    monitor.acknowledge_keys.assert_called_once_with([event.key])


@patch("ready_jobs_watcher.alert_coordinator.send_notification")
@patch.object(AlertCoordinator, "_play_sound")
def test_submit_events_dispatches_popup_toast_sound(mock_sound, mock_toast):
    cfg = Config()
    cfg.bad_parts_popup_enabled = True
    cfg.bad_parts_toast_enabled = True
    cfg.bad_parts_sound_profile = "triple_beep"

    monitor = MagicMock()
    popup = MagicMock()
    coordinator = AlertCoordinator(cfg, monitor, popup_notifier=popup)
    coordinator.start()
    coordinator.submit_events([_sample_event()])

    # Wait briefly for worker thread dispatch.
    time.sleep(0.25)
    coordinator.stop()

    assert popup.call_count == 1
    assert mock_toast.call_count == 1
    assert mock_sound.call_count == 1
