from unittest import mock
import threading
from ready_jobs_watcher.scheduler import process_metadata_end_of_day_once, metadata_end_of_day_scheduler


class FakeMetadataRefreshService:
    def __init__(self):
        self.calls = []

    def run_scheduled_sweep(self, *, consolidate_trackers=True, compact_tracker_events=False):
        self.calls.append((consolidate_trackers, compact_tracker_events))
        return {"processed": 2, "rebuilt": 1, "archived": 2, "errors": 0}


def test_process_metadata_end_of_day_once_runs_condensing_sweep():
    service = FakeMetadataRefreshService()

    result = process_metadata_end_of_day_once(service)

    assert service.calls == [(True, True)]
    assert result["rebuilt"] == 1


def test_metadata_end_of_day_scheduler_calls_moldings_sync(monkeypatch):
    # Mock config
    config = mock.Mock()
    config.metadata_end_of_day_time = "20:00"

    # Mock stop_event to run the loop exactly once
    stop_event = mock.Mock(spec=threading.Event)
    is_set_calls = [0]
    def mock_is_set():
        if is_set_calls[0] > 0:
            return True
        is_set_calls[0] += 1
        return False
    stop_event.is_set.side_effect = mock_is_set
    stop_event.wait.return_value = False

    # Mock service
    service = mock.Mock()

    # Mock process_metadata_end_of_day_once
    process_called = []
    def mock_process(svc):
        process_called.append(svc)
        return {"processed": 1}
    monkeypatch.setattr("ready_jobs_watcher.scheduler.process_metadata_end_of_day_once", mock_process)

    # Mock sync_moldings_library
    sync_called = []
    def mock_sync(cfg):
        sync_called.append(cfg)
        return True
    monkeypatch.setattr("ready_jobs_watcher.moldings_sync.sync_moldings_library", mock_sync)

    metadata_end_of_day_scheduler(config, stop_event, service)

    assert process_called == [service]
    assert sync_called == [config]


def test_metadata_end_of_day_scheduler_handles_moldings_sync_failure(monkeypatch):
    # Mock config
    config = mock.Mock()
    config.metadata_end_of_day_time = "20:00"

    # Mock stop_event to run the loop exactly once
    stop_event = mock.Mock(spec=threading.Event)
    is_set_calls = [0]
    def mock_is_set():
        if is_set_calls[0] > 0:
            return True
        is_set_calls[0] += 1
        return False
    stop_event.is_set.side_effect = mock_is_set
    stop_event.wait.return_value = False

    # Mock service
    service = mock.Mock()

    # Mock process_metadata_end_of_day_once
    monkeypatch.setattr("ready_jobs_watcher.scheduler.process_metadata_end_of_day_once", lambda svc: {"processed": 1})

    # Mock sync_moldings_library to raise exception
    def mock_sync_fail(cfg):
        raise RuntimeError("sync database connection failed")
    monkeypatch.setattr("ready_jobs_watcher.moldings_sync.sync_moldings_library", mock_sync_fail)

    # This should run and complete without raising the RuntimeError outside the scheduler
    metadata_end_of_day_scheduler(config, stop_event, service)

