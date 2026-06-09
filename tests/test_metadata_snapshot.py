import json

from ready_jobs_watcher.metadata_snapshot import archive_job_metadata


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_archive_includes_external_sources_and_manifest(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    job = root / "123 - Test Job"
    _write_json(job / ".metadata" / "cache_static.json", {"jobInfo": {"folderName": "123 - Test Job"}})
    _write_json(job / "CNC" / ".metadata" / "123 - Maple.json", {"pages": [{"pageNumber": 1}]})
    _write_json(job / ".metadata" / "admin" / "specialty_items.json", {"items": [{"id": "s1"}]})
    _write_json(root / "production_order.json", ["123 - Test Job"])
    (job / "CNC" / ".metadata" / ".thumbs").mkdir(parents=True)
    (job / "CNC" / ".metadata" / ".thumbs" / "ignored.png").write_bytes(b"ignored")

    result = archive_job_metadata(root, job, archive_root, reason="unit-test")

    assert result.success is True
    manifest = json.loads((result.snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    archived_paths = {entry["relativePath"] for entry in manifest["files"]}
    assert ".metadata/cache_static.json" in archived_paths
    assert "CNC/.metadata/123 - Maple.json" in archived_paths
    assert ".metadata/admin/specialty_items.json" in archived_paths
    assert "../production_order.json" in archived_paths
    assert "CNC/.metadata/.thumbs/ignored.png" not in archived_paths
    assert (result.snapshot_dir / "files" / "__root__" / "production_order.json").exists()


def test_archive_is_append_only(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    job = root / "123 - Test Job"
    _write_json(job / ".metadata" / "cache_static.json", {"version": 1})

    first = archive_job_metadata(root, job, archive_root, reason="first", timestamp="2026-06-09T20:00:00+00:00")
    _write_json(job / ".metadata" / "cache_static.json", {"version": 2})
    second = archive_job_metadata(root, job, archive_root, reason="second", timestamp="2026-06-09T20:00:01+00:00")

    assert first.snapshot_dir != second.snapshot_dir
    assert (first.snapshot_dir / "manifest.json").exists()
    assert (second.snapshot_dir / "manifest.json").exists()
