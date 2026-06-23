import json

from ready_jobs_watcher.sync_conflict_resolver import (
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
