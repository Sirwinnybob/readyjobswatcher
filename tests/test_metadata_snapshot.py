import json
import shutil

from ready_jobs_watcher.metadata_snapshot import archive_job_metadata, prune_orphan_job_archives


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


def test_archive_prunes_old_snapshots_when_retention_is_set(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    job = root / "123 - Test Job"
    cache_file = job / ".metadata" / "cache_static.json"

    _write_json(cache_file, {"version": 1})
    first = archive_job_metadata(
        root,
        job,
        archive_root,
        reason="first",
        timestamp="2026-06-08T20:00:00+00:00",
        retention_days=1,
    )

    _write_json(cache_file, {"version": 2})
    second = archive_job_metadata(
        root,
        job,
        archive_root,
        reason="second",
        timestamp="2026-06-10T20:00:00+00:00",
        retention_days=1,
    )

    assert not first.snapshot_dir.exists()
    assert second.snapshot_dir.exists()
    assert (second.snapshot_dir / "manifest.json").exists()


def test_prune_orphan_job_archives_deletes_missing_live_jobs(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    live_job = root / "200 - Live Job"
    missing_job = root / "100 - Deleted Job"

    _write_json(live_job / ".metadata" / "cache_static.json", {"version": 1})
    _write_json(missing_job / ".metadata" / "cache_static.json", {"version": 1})

    live_snapshot = archive_job_metadata(root, live_job, archive_root, reason="live")
    missing_snapshot = archive_job_metadata(root, missing_job, archive_root, reason="missing")
    shutil.rmtree(missing_job)

    result = prune_orphan_job_archives(root, archive_root)

    assert result["removed"] == 1
    assert live_snapshot.snapshot_dir.exists()
    assert not missing_snapshot.snapshot_dir.exists()


def test_archive_keeps_only_latest_snapshots_when_max_is_set(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    job = root / "123 - Test Job"
    cache_file = job / ".metadata" / "cache_static.json"

    snapshots = []
    for version in range(5):
        _write_json(cache_file, {"version": version})
        snapshots.append(
            archive_job_metadata(
                root,
                job,
                archive_root,
                reason=f"version-{version}",
                timestamp=f"2026-06-10T20:00:0{version}+00:00",
                max_snapshots_per_job=3,
            )
        )

    assert [snapshot.snapshot_dir.exists() for snapshot in snapshots] == [False, False, True, True, True]


def test_archive_limits_snapshots_to_one_per_daypart_when_enabled(tmp_path):
    root = tmp_path / "Ready Jobs"
    archive_root = tmp_path / "archive"
    job = root / "123 - Test Job"
    cache_file = job / ".metadata" / "cache_static.json"

    snapshots = []
    for version, timestamp in enumerate(
        [
            "2026-06-10T08:00:00+00:00",
            "2026-06-10T09:00:00+00:00",
            "2026-06-10T13:00:00+00:00",
            "2026-06-10T20:00:00+00:00",
        ]
    ):
        _write_json(cache_file, {"version": version})
        snapshots.append(
            archive_job_metadata(
                root,
                job,
                archive_root,
                reason=f"version-{version}",
                timestamp=timestamp,
                daypart_limit=True,
            )
        )

    manifests = sorted(archive_root.rglob("manifest.json"))
    slots = [json.loads(path.read_text(encoding="utf-8"))["snapshotSlot"] for path in manifests]

    assert snapshots[0].snapshot_dir == snapshots[1].snapshot_dir
    assert slots == ["morning", "afternoon", "evening"]
