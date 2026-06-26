from ready_jobs_watcher.metadata_refresh import DebouncedMetadataRefreshScheduler


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
