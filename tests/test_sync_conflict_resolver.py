import json

from ready_jobs_watcher import sync_conflict_resolver
from ready_jobs_watcher.sync_conflict_resolver import (
    is_transient_conflict_path,
    resolve_sync_conflict_file,
    scan_and_resolve_sync_conflicts,
)


def test_missing_original_conflict_is_restored(tmp_path):
    conflict = tmp_path / "job_board.sync-conflict-20260622-070801-DRK5N56.json"
    conflict.write_text('{"jobs":[]}', encoding="utf-8")

    result = resolve_sync_conflict_file(conflict, tmp_path)

    assert result.action == "restored_missing_original"
    assert (tmp_path / "job_board.json").read_text(encoding="utf-8") == '{"jobs":[]}'
    assert not conflict.exists()


def test_duplicate_conflict_is_archived_without_overwriting_original(tmp_path):
    original = tmp_path / "tablet_id.txt"
    conflict = tmp_path / "tablet_id.sync-conflict-20260622-070801-DRK5N56.txt"
    original.write_text("SM-X800-1234", encoding="utf-8")
    conflict.write_text("SM-X800-1234", encoding="utf-8")

    result = resolve_sync_conflict_file(conflict, tmp_path)

    assert result.action == "archived_duplicate"
    assert original.read_text(encoding="utf-8") == "SM-X800-1234"
    archived = tmp_path / ".metadata" / "sync_conflicts" / result.archive_id / "tablet_id.txt"
    assert archived.read_text(encoding="utf-8") == "SM-X800-1234"
    manifest = json.loads((archived.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sameContent"] is True


def test_divergent_conflict_is_archived_with_manifest(tmp_path):
    job = tmp_path / "613 - Test Job"
    job.mkdir()
    original = job / "613 - Door Cut List.pdf"
    conflict = job / "613 - Door Cut List.sync-conflict-20260618-081257-FIVFEYJ.pdf"
    original.write_bytes(b"current pdf")
    conflict.write_bytes(b"conflicting pdf")

    result = resolve_sync_conflict_file(conflict, tmp_path)

    assert result.action == "archived_divergent"
    assert original.read_bytes() == b"current pdf"
    archived = job / ".metadata" / "sync_conflicts" / result.archive_id / original.name
    assert archived.read_bytes() == b"conflicting pdf"
    manifest = json.loads((archived.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sameContent"] is False
    assert manifest["originalPath"].endswith("613 - Door Cut List.pdf")


def test_scan_resolves_nested_conflicts(tmp_path):
    job = tmp_path / "613 - Test Job"
    job.mkdir()
    (job / "613 - Door Cut List.pdf").write_bytes(b"current")
    (job / "613 - Door Cut List.sync-conflict-20260618-081257-FIVFEYJ.pdf").write_bytes(b"other")

    results = scan_and_resolve_sync_conflicts(tmp_path)

    assert [result.action for result in results] == ["archived_divergent"]
    assert not list(tmp_path.rglob("*.sync-conflict-*"))


def test_syncthing_internal_temp_conflict_is_ignored(tmp_path):
    conflict = tmp_path / (
        ".syncthing.delivery_schedule_request.SM-X808U-6448."
        "sync-conflict-20260720-073405-2E2GGMF.json.tmp"
    )
    conflict.write_bytes(b"")
    result = resolve_sync_conflict_file(conflict, tmp_path)
    assert result is None
    assert conflict.exists()
    assert not list((tmp_path / ".metadata" / "sync_conflicts").rglob("manifest*.json"))


def test_regular_request_conflict_is_still_archived(tmp_path):
    original = tmp_path / "delivery_schedule_request.tablet-a.json"
    conflict = tmp_path / "delivery_schedule_request.tablet-a.sync-conflict-20260720-073405-ABC.json"
    original.write_text('{"requestedAt":"old"}', encoding="utf-8")
    conflict.write_text('{"requestedAt":"new"}', encoding="utf-8")
    result = resolve_sync_conflict_file(conflict, tmp_path)
    assert result is not None
    assert result.action == "archived_divergent"
    assert original.exists()
    assert not conflict.exists()


def test_is_transient_conflict_path_detects_all_transient_markers(tmp_path):
    # under an archive dir ("<root>/.metadata/sync_conflicts/...")
    under_archive = (
        tmp_path / ".metadata" / "sync_conflicts" / "20260720-073405-ABC"
        / "foo.sync-conflict-20260720-073405-ABC.json"
    )
    assert is_transient_conflict_path(under_archive) is True

    # under .stversions
    under_stversions = tmp_path / ".stversions" / "foo.sync-conflict-20260720-073405-ABC.json"
    assert is_transient_conflict_path(under_stversions) is True

    # dot-prefixed/internal derived original
    dot_prefixed = tmp_path / ".hidden.sync-conflict-20260720-073405-ABC.json"
    assert is_transient_conflict_path(dot_prefixed) is True

    # starts with .syncthing
    syncthing_prefixed = tmp_path / ".syncthing.foo.sync-conflict-20260720-073405-ABC.json"
    assert is_transient_conflict_path(syncthing_prefixed) is True

    # ends in .tmp
    tmp_suffix = tmp_path / "foo.sync-conflict-20260720-073405-ABC.json.tmp"
    assert is_transient_conflict_path(tmp_suffix) is True

    # ends in .tmp-<suffix>
    tmp_dash_suffix = tmp_path / "foo.sync-conflict-20260720-073405-ABC.json.tmp-99"
    assert is_transient_conflict_path(tmp_dash_suffix) is True

    # ordinary conflict is not transient
    ordinary = tmp_path / "delivery_schedule_request.tablet-a.sync-conflict-20260720-073405-ABC.json"
    assert is_transient_conflict_path(ordinary) is False

    # not even a sync-conflict path at all
    not_a_conflict = tmp_path / "delivery_schedule_request.tablet-a.json"
    assert is_transient_conflict_path(not_a_conflict) is False


def test_scan_ignores_transient_conflicts_without_processing_them(tmp_path):
    transient = tmp_path / (
        ".syncthing.delivery_schedule_request.SM-X808U-6448."
        "sync-conflict-20260720-073405-2E2GGMF.json.tmp"
    )
    transient.write_bytes(b"")
    job = tmp_path / "613 - Test Job"
    job.mkdir()
    (job / "613 - Door Cut List.pdf").write_bytes(b"current")
    (job / "613 - Door Cut List.sync-conflict-20260618-081257-FIVFEYJ.pdf").write_bytes(b"other")

    results = scan_and_resolve_sync_conflicts(tmp_path)

    assert [result.action for result in results] == ["archived_divergent"]
    assert transient.exists()


def test_resolver_skips_conflict_file_still_being_written(tmp_path, monkeypatch):
    original = tmp_path / "job_board.json"
    conflict = tmp_path / "job_board.sync-conflict-20260622-070801-DRK5N56.json"
    original.write_text('{"jobs":[]}', encoding="utf-8")
    conflict.write_text('{"jobs":[]}', encoding="utf-8")

    signatures = iter([(0, 100), (1, 200)])
    monkeypatch.setattr(
        sync_conflict_resolver,
        "_stat_signature",
        lambda path: next(signatures),
    )

    result = resolve_sync_conflict_file(conflict, tmp_path)

    assert result is None
    assert conflict.exists()
    assert original.exists()


def test_resolver_skips_conflict_file_that_disappears_mid_check(tmp_path, monkeypatch):
    original = tmp_path / "job_board.json"
    conflict = tmp_path / "job_board.sync-conflict-20260622-070801-DRK5N56.json"
    original.write_text('{"jobs":[]}', encoding="utf-8")
    conflict.write_text('{"jobs":[]}', encoding="utf-8")

    signatures = iter([(0, 100), None])
    monkeypatch.setattr(
        sync_conflict_resolver,
        "_stat_signature",
        lambda path: next(signatures),
    )

    result = resolve_sync_conflict_file(conflict, tmp_path)

    assert result is None
