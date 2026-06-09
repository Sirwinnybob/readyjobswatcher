from pathlib import Path

from ready_jobs_watcher.metadata_inventory import (
    OwnershipMode,
    classify_metadata_path,
    is_rebuild_trigger,
)


def test_classifies_hours_tracker_metadata_as_external_source(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "123 - Test Job"
    specialty = job / ".metadata" / "admin" / "specialty_items.json"
    supply = root / ".metadata" / "supply.json"
    production_order = root / "production_order.json"

    assert classify_metadata_path(specialty, root).ownership == OwnershipMode.EXTERNAL_SOURCE
    assert classify_metadata_path(supply, root).ownership == OwnershipMode.EXTERNAL_SOURCE
    assert classify_metadata_path(production_order, root).ownership == OwnershipMode.EXTERNAL_SOURCE


def test_classifies_ready_jobs_cache_as_derived_owned_but_not_a_trigger(tmp_path):
    root = tmp_path / "Ready Jobs"
    cache = root / "123 - Test Job" / ".metadata" / "cache_static.json"

    classification = classify_metadata_path(cache, root)

    assert classification.ownership == OwnershipMode.DERIVED_OWNED
    assert is_rebuild_trigger(cache, root) is False


def test_unknown_json_under_metadata_is_external_source_trigger(tmp_path):
    root = tmp_path / "Ready Jobs"
    future_file = root / "123 - Test Job" / ".metadata" / "future_signal.json"

    classification = classify_metadata_path(future_file, root)

    assert classification.ownership == OwnershipMode.EXTERNAL_SOURCE
    assert is_rebuild_trigger(future_file, root) is True


def test_temporary_and_archive_files_are_ignored(tmp_path):
    root = tmp_path / "Ready Jobs"
    tmp_file = root / "123 - Test Job" / ".metadata" / "cache_static.json.tmp"
    thumb = root / "123 - Test Job" / "CNC" / ".metadata" / ".thumbs" / "sheet_p001.png"

    assert classify_metadata_path(tmp_file, root).ownership == OwnershipMode.IGNORED_GENERATED
    assert classify_metadata_path(thumb, root).ownership == OwnershipMode.IGNORED_GENERATED
    assert is_rebuild_trigger(tmp_file, root) is False
    assert is_rebuild_trigger(thumb, root) is False
