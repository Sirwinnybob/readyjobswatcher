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
