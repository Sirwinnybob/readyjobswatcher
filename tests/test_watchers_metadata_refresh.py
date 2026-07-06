from types import SimpleNamespace

import ready_jobs_watcher.watchers as watchers
from ready_jobs_watcher.watchers import PdfChangeHandler


class FakeMetadataRefresh:
    def __init__(self):
        self.calls = []

    def schedule_path(self, path, reason):
        self.calls.append((path, reason))
        return True


def test_pdf_change_handler_schedules_metadata_refresh_for_json_changes(tmp_path):
    refresh = FakeMetadataRefresh()
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config, metadata_refresh_service=refresh)
    path = tmp_path / "123 - Test Job" / "CNC" / ".metadata" / "123 - Maple.json"

    handler.on_created(SimpleNamespace(is_directory=False, src_path=str(path)))

    assert refresh.calls == [(str(path), "created")]


def test_tracker_json_change_schedules_tracker_metadata_refresh(tmp_path):
    refresh = FakeMetadataRefresh()
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config, metadata_refresh_service=refresh)
    tracker_scans = []
    handler._trigger_tracker_scan = lambda reason, src_path: tracker_scans.append((reason, src_path))  # type: ignore[method-assign]
    path = tmp_path / "123 - Test Job" / "CNC" / ".tracker" / "sheet.json"

    handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(path)))

    assert refresh.calls == [(str(path), "tracker_modified")]
    assert tracker_scans == [("tracker_modified", str(path))]


def test_tracker_ndjson_change_schedules_tracker_metadata_refresh(tmp_path):
    refresh = FakeMetadataRefresh()
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config, metadata_refresh_service=refresh)
    tracker_scans = []
    handler._trigger_tracker_scan = lambda reason, src_path: tracker_scans.append((reason, src_path))  # type: ignore[method-assign]
    path = tmp_path / "123 - Test Job" / "CNC" / ".tracker" / "events" / "2026-07.ndjson"

    handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(path)))

    assert refresh.calls == [(str(path), "tracker_modified")]
    assert tracker_scans == [("tracker_modified", str(path))]


def test_pdf_change_handler_ignores_watcher_refresh_signal(tmp_path):
    refresh = FakeMetadataRefresh()
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="tracker",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config, metadata_refresh_service=refresh)
    tracker_scans = []
    handler._trigger_tracker_scan = lambda reason, src_path: tracker_scans.append((reason, src_path))  # type: ignore[method-assign]
    path = tmp_path / "123 - Test Job" / "CNC" / ".tracker" / "watcher_refresh_watcher.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")

    handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(path)))

    assert refresh.calls == []
    assert tracker_scans == []


def test_index_refresh_completion_schedules_metadata_refresh(monkeypatch, tmp_path):
    refresh = FakeMetadataRefresh()
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config, metadata_refresh_service=refresh)
    path = tmp_path / "123 - Test Job" / "123 - Assembly Sheets.pdf"

    monkeypatch.setattr(watchers, "build_reference_index_for_pdf_event", lambda pdf_path: None)
    monkeypatch.setattr(watchers, "build_hardwoods_cutlist_index_for_pdf_event", lambda pdf_path, deployment_gate=None: None)

    handler._run_index_refresh(str(path), "created")

    assert refresh.calls == [(str(path), "index_refresh_complete")]


def test_pdf_change_handler_retargets_pending_conversion_timer(monkeypatch, tmp_path):
    timers = []

    class FakeTimer:
        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback
            self.cancelled = False
            self.daemon = False
            self.name = ""
            timers.append(self)

        def start(self):
            return None

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(watchers.threading, "Timer", FakeTimer)
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    handler = PdfChangeHandler(config)
    old_path = str(tmp_path / "123 - OLD" / "123 - Assembly Sheets.pdf")
    new_path = str(tmp_path / "999 - NEW" / "999 - Assembly Sheets.pdf")

    handler._schedule_pdf_conversion(old_path, invert_images=True, delay_seconds=45)
    handler.retarget_pending_pdf_conversions({old_path: new_path})

    assert len(timers) == 2
    assert timers[0].cancelled is True
    assert timers[1].cancelled is False
    assert timers[1].delay == 45


def test_pdf_delete_with_dark_mode_copy_removes_pending_queue(monkeypatch, tmp_path):
    removed = []
    config = SimpleNamespace(
        ROOT_DIR=str(tmp_path),
        bad_parts_mode="legacy",
        pdf_conversion_delay_seconds=30,
    )
    pending_queue = SimpleNamespace(remove_pending_pdf=lambda path: removed.append(path))
    handler = PdfChangeHandler(config, pending_queue=pending_queue)
    path = tmp_path / "123 - Test Job" / "123 - Assembly Sheets.pdf"
    dark = path.parent / "DARK MODE" / path.name
    dark.parent.mkdir(parents=True)
    dark.write_text("dark", encoding="utf-8")

    monkeypatch.setattr(watchers, "build_reference_index_for_pdf_event", lambda pdf_path: None)
    monkeypatch.setattr(watchers, "build_hardwoods_cutlist_index_for_pdf_event", lambda pdf_path, deployment_gate=None: None)

    handler.on_deleted(SimpleNamespace(is_directory=False, src_path=str(path)))

    assert not dark.exists()
    assert removed == [str(path)]
