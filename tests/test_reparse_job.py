import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ready_jobs_watcher import cutlist_job_mismatch
import ready_jobs_watcher.hardwoods_cutlist_indexer as cutlist_indexer
import ready_jobs_watcher.main as main
from ready_jobs_watcher.main import Application
from ready_jobs_watcher.deployment_gate import DEPLOYMENT_GATE_FILENAME


def _cutlist_override_app(root_dir):
    app = Application.__new__(Application)
    app.config = SimpleNamespace(ROOT_DIR=str(root_dir))
    app.deployment_gate = MagicMock()
    app.metadata_refresh_service = MagicMock()
    app.settings_window = MagicMock()
    return app


def _cutlist_override_identity():
    return {
        "doc_type": "NAILER_CUT_LIST",
        "pdf_filename": "530a - Nailer Cut List.pdf",
        "expected_job": "530A",
        "found_job": "532",
    }


def _cutlist_override_identity_tuple():
    identity = _cutlist_override_identity()
    return (
        identity["doc_type"],
        identity["pdf_filename"],
        identity["expected_job"],
        identity["found_job"],
    )


def _cutlist_rebuild_result(*, success=True, active=True):
    identity = _cutlist_override_identity()
    active_identities = (
        frozenset({
            (
                identity["doc_type"],
                identity["pdf_filename"],
                identity["expected_job"],
                identity["found_job"],
            )
        })
        if active
        else frozenset()
    )
    return cutlist_indexer.HardwoodsIndexBuildResult(
        success=success,
        changed=success,
        active_override_identities=active_identities,
    )


def _seed_cutlist_publication_outputs(job_path, *, include_hardwoods_outputs=True):
    metadata_dir = job_path / ".metadata"
    hardwoods_dir = metadata_dir / "hardwoods"
    hardwoods_dir.mkdir(parents=True, exist_ok=True)
    snapshots = {}
    if include_hardwoods_outputs:
        snapshots.update({
            hardwoods_dir / "cutlist_index.json": (
                b'{\r\n  "generatedAt": "before",\r\n  "documents": []\r\n}\r\n'
            ),
            hardwoods_dir / "cutlist_job_mismatch.json": (
                b'{\r\n  "updatedAt": "before",\r\n  "mismatches": []\r\n}\r\n'
            ),
            hardwoods_dir / "cutlist_revisions.json": (
                b'{\r\n  "schemaVersion": 1,\r\n  "currentRevision": 7,\r\n'
                b'  "revisions": [{"revision": 7}],\r\n  "currentRowStates": []\r\n}\r\n'
            ),
        })
    snapshots[metadata_dir / "cache_static.json"] = b'{"before": "cache"}\r\n'
    for path, content in snapshots.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return snapshots


def _assert_publication_outputs_unchanged(snapshots):
    for path, content in snapshots.items():
        assert path.read_bytes() == content


class _FakePdfPage:
    def __init__(self, words):
        self._words = words

    def get_text(self, mode="text", *_args, **_kwargs):
        return self._words if mode == "words" else ""


class _FakePdfDocument:
    def __init__(self, pages):
        self._pages = pages
        self.page_count = len(pages)

    def __getitem__(self, index):
        return self._pages[index]

    def close(self):
        return None


def _pdf_word(x, y, text):
    return (float(x), float(y), float(x) + 8.0, float(y) + 8.0, text, 0, 0, 0)


def _cutlist_table_words(job_number, *, description="Bottom Rail"):
    words = [
        _pdf_word(80, 130, str(job_number)),
        _pdf_word(100, 130, "-"),
        _pdf_word(112, 130, "TEST"),
        _pdf_word(74, 145, "Material:"),
        _pdf_word(124, 145, "'3/4"),
        _pdf_word(160, 145, "Maple'"),
        _pdf_word(80, 160, "Qty"),
        _pdf_word(108, 160, "|"),
        _pdf_word(114, 160, "Description"),
        _pdf_word(166, 160, "|"),
        _pdf_word(278, 160, "|"),
        _pdf_word(284, 160, "Width"),
        _pdf_word(322, 160, "*"),
        _pdf_word(336, 160, "Length"),
        _pdf_word(369, 160, "|"),
        _pdf_word(468, 160, "|"),
        _pdf_word(474, 160, "Cabinet"),
        _pdf_word(510, 160, "(Qty)"),
        _pdf_word(534, 160, "|"),
        _pdf_word(86, 182, "1"),
        _pdf_word(108, 182, "|"),
        _pdf_word(114, 182, description),
        _pdf_word(166, 182, "|"),
        _pdf_word(278, 182, "|"),
        _pdf_word(286, 182, "4.75"),
        _pdf_word(322, 182, "*"),
        _pdf_word(336, 182, "54"),
        _pdf_word(369, 182, "|"),
        _pdf_word(468, 182, "|"),
        _pdf_word(500, 182, "15"),
        _pdf_word(534, 182, "|"),
    ]
    return words


def test_update_cutlist_override_rebuilds_job_then_refreshes_cache(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)
    rebuilt_paths = []

    def rebuild(path, **kwargs):
        rebuilt_paths.append((path, kwargs))
        return _cutlist_rebuild_result()

    monkeypatch.setattr(main, "build_hardwoods_cutlist_index_result_for_job", rebuild)

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **_cutlist_override_identity())

    assert result == {"success": True, "message": "Cutlist mismatch override updated and cache refreshed."}
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    assert rebuilt_paths == [
        (
            str(job_path),
            {
                "deployment_gate": app.deployment_gate,
                "on_job_mismatch": app._queue_job_mismatch_notice,
                "required_override_identity": _cutlist_override_identity_tuple(),
            },
        )
    ]
    app.metadata_refresh_service.refresh_job_now.assert_called_once_with(
        Path(job_path), "cutlist_job_mismatch_override_updated"
    )
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_called_once()


def test_update_cutlist_override_fails_when_target_retry_fails_despite_valid_sibling(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    target = job_path / "530a - Nailer Cut List.pdf"
    target.write_text("placeholder", encoding="utf-8")
    sibling = job_path / "530a - Face Frame Cut List.pdf"
    sibling.write_text("placeholder", encoding="utf-8")
    app = _cutlist_override_app(tmp_path)
    snapshots = _seed_cutlist_publication_outputs(job_path)

    target_opens = [
        _FakePdfDocument([_FakePdfPage(_cutlist_table_words("532"))]),
        _FakePdfDocument([_FakePdfPage([_pdf_word(80, 130, "unparseable")])]),
    ]

    def open_pdf(path):
        if str(path) == str(target):
            return target_opens.pop(0)
        return _FakePdfDocument([_FakePdfPage(_cutlist_table_words("530a", description="Face Rail"))])

    monkeypatch.setattr(cutlist_indexer.fitz, "open", open_pdf)

    result = app.update_cutlist_job_mismatch_override(
        job_name,
        allow=True,
        **_cutlist_override_identity(),
    )

    assert result == {
        "success": False,
        "message": "Override saved, but the selected PDF was not indexed.",
    }
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    payload = cutlist_job_mismatch.read_job_mismatch_flags(str(tmp_path), job_name)
    assert payload is not None
    target_status = next(
        entry for entry in payload["mismatches"]
        if entry.get("pdfFilename") == target.name and entry.get("foundJob") == "532"
    )
    assert target_status["overridePresent"] is True
    assert target_status.get("overrideActive", False) is False
    _assert_publication_outputs_unchanged(snapshots)
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


@pytest.mark.parametrize("replacement_job", ["UNNUMBERED", "530a"])
def test_update_cutlist_override_rejects_retry_without_exact_approved_identity(
    tmp_path,
    monkeypatch,
    replacement_job,
):
    job_name, job_path, target = _wrong_job_target_for_publication_test(tmp_path)
    app = _cutlist_override_app(tmp_path)
    snapshots = _seed_cutlist_publication_outputs(job_path)
    target_opens = [
        _FakePdfDocument([_FakePdfPage(_cutlist_table_words("532"))]),
        _FakePdfDocument([_FakePdfPage(_cutlist_table_words(replacement_job))]),
    ]
    monkeypatch.setattr(
        cutlist_indexer.fitz,
        "open",
        lambda path: target_opens.pop(0) if str(path) == str(target) else None,
    )

    result = app.update_cutlist_job_mismatch_override(
        job_name,
        allow=True,
        **_cutlist_override_identity(),
    )

    assert result == {
        "success": False,
        "message": "Override saved, but the selected PDF was not indexed.",
    }
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    _assert_publication_outputs_unchanged(snapshots)
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def _wrong_job_target_for_publication_test(tmp_path):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    target = job_path / "530a - Nailer Cut List.pdf"
    target.write_text("placeholder", encoding="utf-8")
    return job_name, job_path, target


def test_update_cutlist_override_stops_before_cache_when_index_publication_fails(tmp_path, monkeypatch):
    job_name, job_path, _target = _wrong_job_target_for_publication_test(tmp_path)
    app = _cutlist_override_app(tmp_path)
    monkeypatch.setattr(
        cutlist_indexer.fitz,
        "open",
        lambda _path: _FakePdfDocument([_FakePdfPage(_cutlist_table_words("532"))]),
    )
    monkeypatch.setattr(cutlist_indexer, "_write_index", lambda *_args, **_kwargs: None)

    result = app.update_cutlist_job_mismatch_override(
        job_name,
        allow=True,
        **_cutlist_override_identity(),
    )

    assert result == {
        "success": False,
        "message": "Override saved, but hardwoods rebuild did not complete.",
    }
    assert not (job_path / ".metadata" / "hardwoods" / "cutlist_revisions.json").exists()
    payload = cutlist_job_mismatch.read_job_mismatch_flags(str(tmp_path), job_name)
    assert payload is not None
    target_status = next(
        entry for entry in payload["mismatches"]
        if entry.get("pdfFilename") == _cutlist_override_identity()["pdf_filename"]
    )
    assert target_status["overridePresent"] is True
    assert target_status["overrideActive"] is False
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_stops_before_index_when_status_publication_fails(tmp_path, monkeypatch):
    job_name, job_path, _target = _wrong_job_target_for_publication_test(tmp_path)
    app = _cutlist_override_app(tmp_path)
    monkeypatch.setattr(
        cutlist_indexer.fitz,
        "open",
        lambda _path: _FakePdfDocument([_FakePdfPage(_cutlist_table_words("532"))]),
    )
    monkeypatch.setattr(cutlist_indexer, "_write_mismatch_flags", lambda *_args, **_kwargs: False)

    result = app.update_cutlist_job_mismatch_override(
        job_name,
        allow=True,
        **_cutlist_override_identity(),
    )

    assert result == {
        "success": False,
        "message": "Override saved, but hardwoods rebuild did not complete.",
    }
    assert not (job_path / ".metadata" / "hardwoods" / "cutlist_index.json").exists()
    assert not (job_path / ".metadata" / "hardwoods" / "cutlist_revisions.json").exists()
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


@pytest.mark.parametrize("existing_outputs", [True, False])
def test_update_cutlist_override_rolls_back_index_and_status_when_revision_publication_fails(
    tmp_path,
    monkeypatch,
    existing_outputs,
):
    job_name, job_path, _target = _wrong_job_target_for_publication_test(tmp_path)
    app = _cutlist_override_app(tmp_path)
    snapshots = _seed_cutlist_publication_outputs(
        job_path,
        include_hardwoods_outputs=existing_outputs,
    )
    hardwoods_dir = job_path / ".metadata" / "hardwoods"
    index_path = hardwoods_dir / "cutlist_index.json"
    status_path = hardwoods_dir / "cutlist_job_mismatch.json"
    revision_path = hardwoods_dir / "cutlist_revisions.json"
    monkeypatch.setattr(
        cutlist_indexer.fitz,
        "open",
        lambda _path: _FakePdfDocument([_FakePdfPage(_cutlist_table_words("532"))]),
    )
    monkeypatch.setattr(cutlist_indexer, "_upsert_revision_state", lambda *_args, **_kwargs: None)

    result = app.update_cutlist_job_mismatch_override(
        job_name,
        allow=True,
        **_cutlist_override_identity(),
    )

    assert result == {
        "success": False,
        "message": "Override saved, but hardwoods rebuild did not complete.",
    }
    if existing_outputs:
        _assert_publication_outputs_unchanged(snapshots)
    else:
        assert not index_path.exists()
        assert not status_path.exists()
        assert not revision_path.exists()
        assert (job_path / ".metadata" / "cache_static.json").read_bytes() == snapshots[
            job_path / ".metadata" / "cache_static.json"
        ]
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_revoke_waits_for_watcher_build_then_prevents_stale_override_republication(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    target = job_path / "530a - Nailer Cut List.pdf"
    target.write_text("placeholder", encoding="utf-8")
    sibling = job_path / "530a - Face Frame Cut List.pdf"
    sibling.write_text("placeholder", encoding="utf-8")
    app = _cutlist_override_app(tmp_path)
    identity = _cutlist_override_identity()
    assert cutlist_job_mismatch.allow_job_mismatch_override(str(job_path), **identity)

    def open_pdf(path):
        job_number = "532" if str(path) == str(target) else "530a"
        description = "Nailer Rail" if str(path) == str(target) else "Face Rail"
        return _FakePdfDocument([_FakePdfPage(_cutlist_table_words(job_number, description=description))])

    monkeypatch.setattr(cutlist_indexer.fitz, "open", open_pdf)
    watcher_at_index_write = threading.Event()
    release_watcher = threading.Event()
    real_write_index = cutlist_indexer._write_index
    write_count = 0
    write_count_lock = threading.Lock()

    def coordinated_write_index(*args, **kwargs):
        nonlocal write_count
        with write_count_lock:
            write_count += 1
            call_number = write_count
        if call_number == 1:
            watcher_at_index_write.set()
            assert release_watcher.wait(timeout=2)
        return real_write_index(*args, **kwargs)

    monkeypatch.setattr(cutlist_indexer, "_write_index", coordinated_write_index)
    watcher_done = threading.Event()
    revoke_done = threading.Event()
    revoke_result = {}

    def run_watcher():
        cutlist_indexer.build_hardwoods_cutlist_index_for_job(str(job_path))
        watcher_done.set()

    def run_revoke():
        revoke_result.update(app.update_cutlist_job_mismatch_override(
            job_name,
            allow=False,
            **identity,
        ))
        revoke_done.set()

    watcher_thread = threading.Thread(target=run_watcher)
    revoke_thread = threading.Thread(target=run_revoke)
    watcher_thread.start()
    assert watcher_at_index_write.wait(timeout=2)
    revoke_thread.start()
    revoke_completed_before_watcher = revoke_done.wait(timeout=0.3)
    release_watcher.set()
    watcher_thread.join(timeout=3)
    revoke_thread.join(timeout=3)

    assert not watcher_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert revoke_completed_before_watcher is False
    assert watcher_done.is_set()
    assert revoke_result["success"] is True
    assert not cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **identity)
    with open(job_path / ".metadata" / "hardwoods" / "cutlist_index.json", encoding="utf-8") as f:
        index_payload = json.load(f)
    assert [doc["pdfFilename"] for doc in index_payload["documents"]] == [sibling.name]
    payload = cutlist_job_mismatch.read_job_mismatch_flags(str(tmp_path), job_name)
    assert payload is not None
    assert not any(entry.get("overrideActive") for entry in payload["mismatches"])


def test_update_cutlist_override_keeps_allow_decision_when_rebuild_fails(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)

    def rebuild(*args, **kwargs):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(main, "build_hardwoods_cutlist_index_result_for_job", rebuild)

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **_cutlist_override_identity())

    assert result == {"success": False, "message": "Override saved, but rebuild failed: parser unavailable"}
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_does_not_bypass_document_type_mismatch_or_refresh_cache(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    pdf_path = job_path / "530a - Face Frame Cut List.pdf"
    pdf_path.write_text("placeholder", encoding="utf-8")
    app = _cutlist_override_app(tmp_path)
    fixture_identity = {
        "doc_type": cutlist_indexer.DOC_TYPE_FACE_FRAME,
        "pdf_filename": pdf_path.name,
        "expected_job": "530A",
        "found_job": "532",
    }

    words = [
        _pdf_word(74, 100, "Door"),
        _pdf_word(108, 100, "Cut"),
        _pdf_word(132, 100, "List"),
        _pdf_word(168, 100, "3.0"),
        _pdf_word(80, 130, "532"),
        _pdf_word(100, 130, "-"),
        _pdf_word(112, 130, "WRONG"),
        _pdf_word(160, 130, "JOB"),
    ]
    monkeypatch.setattr(
        cutlist_indexer.fitz,
        "open",
        lambda path: _FakePdfDocument([_FakePdfPage(words)]),
    )

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **fixture_identity)

    assert result == {
        "success": False,
        "message": "Override saved, but hardwoods rebuild did not complete.",
    }
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **fixture_identity)
    assert not (job_path / ".metadata" / "hardwoods" / "cutlist_index.json").exists()
    assert not (job_path / ".metadata" / "hardwoods" / "cutlist_revisions.json").exists()
    assert not Path(cutlist_job_mismatch.mismatch_flag_path(str(job_path))).exists()
    visible_status = cutlist_job_mismatch.read_job_mismatch_flags(str(tmp_path), job_name)
    assert visible_status is not None
    assert len(visible_status["mismatches"]) == 1
    saved_status = visible_status["mismatches"][0]
    assert saved_status["docType"] == cutlist_indexer.DOC_TYPE_FACE_FRAME
    assert saved_status["pdfFilename"] == "530a - Face Frame Cut List.pdf"
    assert saved_status["expectedJob"] == "530A"
    assert saved_status["foundJob"] == "532"
    assert saved_status["overridePresent"] is True
    assert saved_status["overrideActive"] is False
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_keeps_allow_decision_when_rebuild_returns_false(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)
    monkeypatch.setattr(
        main,
        "build_hardwoods_cutlist_index_result_for_job",
        lambda *args, **kwargs: _cutlist_rebuild_result(success=False, active=False),
    )

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **_cutlist_override_identity())

    assert result == {"success": False, "message": "Override saved, but hardwoods rebuild did not complete."}
    assert cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_keeps_revoke_decision_when_refresh_fails(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)
    cutlist_job_mismatch.allow_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    monkeypatch.setattr(
        main,
        "build_hardwoods_cutlist_index_result_for_job",
        lambda *args, **kwargs: _cutlist_rebuild_result(active=False),
    )
    app.metadata_refresh_service.refresh_job_now.side_effect = RuntimeError("cache unavailable")

    result = app.update_cutlist_job_mismatch_override(job_name, allow=False, **_cutlist_override_identity())

    assert result == {"success": False, "message": "Override removed, but cache refresh failed: cache unavailable"}
    assert not cutlist_job_mismatch.has_job_mismatch_override(str(job_path), **_cutlist_override_identity())
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_returns_failure_when_allow_write_raises(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)
    rebuild_calls = []

    def write_override(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(main, "allow_job_mismatch_override", write_override)
    monkeypatch.setattr(
        main,
        "build_hardwoods_cutlist_index_result_for_job",
        lambda *args, **kwargs: rebuild_calls.append((args, kwargs)) or _cutlist_rebuild_result(),
    )

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **_cutlist_override_identity())

    assert result == {"success": False, "message": "Override could not be saved: disk full"}
    assert rebuild_calls == []
    app.metadata_refresh_service.refresh_job_now.assert_not_called()
    app.metadata_refresh_service.schedule_job.assert_not_called()
    app.settings_window.refresh_jobs_dashboard.assert_not_called()


def test_update_cutlist_override_stops_when_allow_decision_cannot_be_saved(tmp_path, monkeypatch):
    job_name = "530a - TEST"
    job_path = tmp_path / job_name
    job_path.mkdir()
    app = _cutlist_override_app(tmp_path)
    override_path = cutlist_job_mismatch.mismatch_override_path(str(job_path))
    Path(override_path).parent.mkdir(parents=True)
    Path(override_path).write_text("not valid json", encoding="utf-8")
    rebuild_calls = []
    monkeypatch.setattr(
        main,
        "build_hardwoods_cutlist_index_result_for_job",
        lambda *args, **kwargs: rebuild_calls.append((args, kwargs)) or _cutlist_rebuild_result(),
    )

    result = app.update_cutlist_job_mismatch_override(job_name, allow=True, **_cutlist_override_identity())

    assert result == {"success": False, "message": "Override could not be saved."}
    assert rebuild_calls == []
    app.metadata_refresh_service.refresh_job_now.assert_not_called()


class TestReparseJob(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("ready_jobs_watcher.main.clear_old_logs")
    @patch("ready_jobs_watcher.main.build_reference_index_for_job")
    @patch("ready_jobs_watcher.main.build_hardwoods_cutlist_index_for_job")
    @patch("ready_jobs_watcher.main.convert_3d_models_for_job")
    @patch("ready_jobs_watcher.pdf_dark_mode.process_directory")
    def test_reparse_job_cleans_and_calls_parsers(
        self,
        mock_process_directory,
        mock_convert_3d,
        mock_build_hardwoods,
        mock_build_reference,
        mock_clear_logs
    ):
        # Setup mock return values
        mock_build_reference.return_value = True
        mock_build_hardwoods.return_value = True

        # Instantiate Application
        # Mock config's ROOT_DIR to be our temp directory
        with patch("ready_jobs_watcher.main.Config") as mock_config_class:
            mock_config = MagicMock()
            mock_config.ROOT_DIR = self.root
            mock_config.CNC_SUBDIR = "CNC"
            mock_config_class.return_value = mock_config

            app = Application()

            # Create mock job folder structure
            job_name = "123-TEST_JOB"
            job_path = os.path.join(self.root, job_name)
            os.makedirs(job_path, exist_ok=True)

            # Create directories to be deleted
            dark_mode_dir = os.path.join(job_path, "DARK MODE")
            os.makedirs(dark_mode_dir, exist_ok=True)
            with open(os.path.join(dark_mode_dir, "some_inverted.pdf"), "w") as f:
                f.write("pdf data")

            cnc_metadata_dir = os.path.join(job_path, "CNC", ".metadata")
            os.makedirs(cnc_metadata_dir, exist_ok=True)
            candidates_file_path = os.path.join(cnc_metadata_dir, "remake_bad_parts_candidates.json")
            with open(candidates_file_path, "w") as f:
                f.write("candidates")
            # Create a file that should NOT be deleted (external metadata)
            external_cnc_metadata_file = os.path.join(cnc_metadata_dir, "sheet_1.json")
            with open(external_cnc_metadata_file, "w") as f:
                f.write("external sheet metadata")

            three_d_dir = os.path.join(job_path, "3D", "Room_1")
            os.makedirs(three_d_dir, exist_ok=True)
            glb_file_path = os.path.join(three_d_dir, "3d_medium.glb")
            with open(glb_file_path, "w") as f:
                f.write("glb data")

            metadata_dir = os.path.join(job_path, ".metadata")
            os.makedirs(metadata_dir, exist_ok=True)
            with open(os.path.join(metadata_dir, DEPLOYMENT_GATE_FILENAME), "w") as f:
                f.write('{"deployed": true, "parseReady": true}')
            with open(os.path.join(metadata_dir, "cabinet_sheet_index.json"), "w") as f:
                f.write("index data")
            with open(os.path.join(metadata_dir, "cache_static.json"), "w") as f:
                f.write("cache data")

            # Setup App settings window mock
            app.settings_window = MagicMock()

            # Run reparse_job
            app.job_processor = MagicMock()
            result = app.reparse_job(job_name)

            # Assertions
            self.assertTrue(result)

            # Check that files/dirs were deleted
            self.assertFalse(os.path.exists(dark_mode_dir))
            self.assertFalse(os.path.exists(candidates_file_path))
            self.assertTrue(os.path.exists(external_cnc_metadata_file)) # Ensure external file is preserved
            self.assertFalse(os.path.exists(glb_file_path))
            self.assertFalse(os.path.exists(os.path.join(metadata_dir, "cabinet_sheet_index.json")))
            self.assertFalse(os.path.exists(os.path.join(metadata_dir, "cache_static.json")))

            # Check that deployment_gate.json was preserved
            self.assertTrue(os.path.exists(os.path.join(metadata_dir, DEPLOYMENT_GATE_FILENAME)))

            # Check that mock parsers were called
            app.job_processor.process_job_folder.assert_called_once_with(job_path)
            mock_build_reference.assert_called_once_with(job_path)
            mock_build_hardwoods.assert_called_once_with(
                job_path, deployment_gate=app.deployment_gate, on_job_mismatch=app._queue_job_mismatch_notice
            )
            mock_convert_3d.assert_called_once_with(job_path)
            mock_process_directory.assert_called_once_with(job_path, force=True)

            # Check deployment gate state updates (parseReady should end up True since build functions returned True)
            state = app.deployment_gate.load_state(job_name)
            self.assertTrue(state["parseReady"])

            # Check settings GUI was refreshed
            app.settings_window.refresh_jobs_dashboard.assert_called_once()

if __name__ == "__main__":
    unittest.main()

