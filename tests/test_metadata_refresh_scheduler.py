from ready_jobs_watcher.metadata_refresh import DebouncedMetadataRefreshScheduler
from ready_jobs_watcher.metadata_refresh import MetadataRefreshService


class FakeTimer:
    instances = []

    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.daemon = False
        self.name = ""
        FakeTimer.instances.append(self)

    def start(self):
        return None

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


def test_refresh_timer_batches_changes_without_resetting_window(tmp_path):
    FakeTimer.instances = []
    calls = []
    scheduler = DebouncedMetadataRefreshScheduler(
        root_dir=tmp_path,
        refresh_callback=lambda job_path, reason: calls.append((job_path.name, reason)),
        delay_seconds=8,
        timer_factory=FakeTimer,
    )
    job = tmp_path / "123 - Test Job"

    scheduler.schedule(job, "sidecar_created")
    scheduler.schedule(job, "ocr_complete")

    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].delay == 8
    FakeTimer.instances[0].fire()
    assert calls == [("123 - Test Job", "ocr_complete")]


def test_scheduler_ignores_generated_cache_file(tmp_path):
    FakeTimer.instances = []
    calls = []
    scheduler = DebouncedMetadataRefreshScheduler(
        root_dir=tmp_path,
        refresh_callback=lambda job_path, reason: calls.append((job_path.name, reason)),
        delay_seconds=8,
        timer_factory=FakeTimer,
    )
    cache_path = tmp_path / "123 - Test Job" / ".metadata" / "cache_static.json"

    assert scheduler.schedule_for_changed_path(cache_path, "cache_write") is False
    assert FakeTimer.instances == []
    assert calls == []


def test_root_production_order_change_debounces_global_refresh(tmp_path):
    FakeTimer.instances = []
    job_calls = []
    global_calls = []
    scheduler = DebouncedMetadataRefreshScheduler(
        root_dir=tmp_path,
        refresh_callback=lambda job_path, reason: job_calls.append((job_path.name, reason)),
        refresh_all_callback=lambda reason: global_calls.append(reason),
        delay_seconds=8,
        timer_factory=FakeTimer,
    )
    order_path = tmp_path / "production_order.json"

    assert scheduler.schedule_for_changed_path(order_path, "production_order_updated") is True

    FakeTimer.instances[0].fire()
    assert job_calls == []
    assert global_calls == ["production_order_updated"]


def test_global_refresh_uses_latest_empty_reason(tmp_path):
    FakeTimer.instances = []
    global_calls = []
    scheduler = DebouncedMetadataRefreshScheduler(
        root_dir=tmp_path,
        refresh_callback=lambda job_path, reason: None,
        refresh_all_callback=lambda reason: global_calls.append(reason),
        delay_seconds=8,
        timer_factory=FakeTimer,
    )

    scheduler.schedule_all("production_order_updated")
    scheduler.schedule_all("")

    FakeTimer.instances[0].fire()

    assert global_calls == [""]


def test_tracker_reason_refresh_consolidates_trackers(monkeypatch, tmp_path):
    calls = []

    def fake_refresh_single_job(root_dir, job_folder, **kwargs):
        calls.append((root_dir, job_folder, kwargs))

    monkeypatch.setattr("ready_jobs_watcher.metadata_refresh.refresh_single_job", fake_refresh_single_job)
    config = type(
        "Config",
        (),
        {
            "ROOT_DIR": str(tmp_path),
            "metadata_snapshot_enabled": False,
            "metadata_snapshot_retention_days": 30,
            "metadata_snapshot_max_per_job": 3,
            "metadata_snapshot_daypart_limit": True,
            "metadata_cache_debounce_seconds": 0,
        },
    )()
    service = MetadataRefreshService(config)
    job = tmp_path / "123 - TEST"

    service.refresh_job_now(job, "tracker_modified")

    assert calls
    assert calls[0][2]["consolidate_trackers"] is True


def test_process_metadata_end_of_day_once_compacts_tracker_events(monkeypatch, tmp_path):
    calls = []

    def fake_update_all_jobs_cache(base_path, **kwargs):
        calls.append(kwargs)
        return {"processed": 0, "rebuilt": 0, "archived": 0, "errors": 0}

    monkeypatch.setattr("ready_jobs_watcher.metadata_refresh.update_all_jobs_cache", fake_update_all_jobs_cache)
    config = type(
        "Config",
        (),
        {
            "ROOT_DIR": str(tmp_path),
            "metadata_snapshot_enabled": False,
            "metadata_snapshot_retention_days": 30,
            "metadata_snapshot_max_per_job": 3,
            "metadata_snapshot_daypart_limit": True,
            "metadata_cache_debounce_seconds": 0,
        },
    )()
    service = MetadataRefreshService(config)

    from ready_jobs_watcher.scheduler import process_metadata_end_of_day_once
    process_metadata_end_of_day_once(service)

    assert calls
    assert calls[0]["consolidate_trackers"] is True
    assert calls[0]["compact_tracker_events"] is True


def test_run_scheduled_sweep_defaults_compact_to_false(monkeypatch, tmp_path):
    calls = []

    def fake_update_all_jobs_cache(base_path, **kwargs):
        calls.append(kwargs)
        return {"processed": 0, "rebuilt": 0, "archived": 0, "errors": 0}

    monkeypatch.setattr("ready_jobs_watcher.metadata_refresh.update_all_jobs_cache", fake_update_all_jobs_cache)
    config = type(
        "Config",
        (),
        {
            "ROOT_DIR": str(tmp_path),
            "metadata_snapshot_enabled": False,
            "metadata_snapshot_retention_days": 30,
            "metadata_snapshot_max_per_job": 3,
            "metadata_snapshot_daypart_limit": True,
            "metadata_cache_debounce_seconds": 0,
        },
    )()
    service = MetadataRefreshService(config)

    service.run_scheduled_sweep(consolidate_trackers=True)

    assert calls
    assert calls[0]["compact_tracker_events"] is False
