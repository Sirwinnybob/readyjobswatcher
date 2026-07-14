import json

from ready_jobs_watcher.job_rename import rename_ready_job
from ready_jobs_watcher.job_rename import apply_job_number_prefix


def test_apply_job_number_prefix_swaps_leading_number():
    assert apply_job_number_prefix("456", "123 - Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"


def test_apply_job_number_prefix_prepends_when_no_separator():
    assert apply_job_number_prefix("456", "Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"


def test_apply_job_number_prefix_is_noop_when_already_correct():
    assert apply_job_number_prefix("456", "456 - Assembly Sheets.pdf") == "456 - Assembly Sheets.pdf"


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_rename_ready_job_updates_folder_and_metadata_references(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "123 - OLD CUSTOMER"
    new_name = "456 - NEW CUSTOMER"
    job = root / old_name
    (job / "CNC").mkdir(parents=True)
    (job / "CNC" / "123 - Maple.pdf").write_bytes(b"pdf")

    _write_json(
        root / "production_order.json",
        ["999 - OTHER", old_name],
    )
    _write_json(
        root / "job_board.json",
        {
            "jobs": [
                {
                    "folder_name": old_name,
                    "folderName": old_name,
                    "jobNumber": "123",
                    "label": "keep",
                }
            ]
        },
    )
    _write_json(
        root / ".metadata" / "delivery_schedule.json",
        {"items": [{"folderName": old_name, "title": "Delivery for 123 - OLD CUSTOMER"}]},
    )
    _write_json(
        job / ".metadata" / "deployment_gate.json",
        {"jobFolderName": old_name, "deployed": True, "parseReady": True},
    )
    _write_json(
        job / ".metadata" / "cache_static.json",
        {
            "jobInfo": {
                "folderName": old_name,
                "jobNumber": "123",
                "jobName": "OLD CUSTOMER",
            },
            "cncJob": {
                "folderName": old_name,
                "jobNumber": "123",
                "jobName": "OLD CUSTOMER",
                "materials": [],
            },
        },
    )
    _write_json(
        job / "CNC" / ".metadata" / "123 - Maple.json",
        {
            "jobFolderName": old_name,
            "jobNumber": "123",
            "jobName": "123 - OLD CUSTOMER",
            "pages": [{"sheetSummary": {"job": "123 - OLD CUSTOMER"}}],
        },
    )

    result = rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    assert result.old_name == old_name
    assert result.new_name == new_name
    assert not (root / old_name).exists()
    assert (root / new_name).is_dir()

    assert _read_json(root / "production_order.json") == ["999 - OTHER", new_name]
    job_board = _read_json(root / "job_board.json")
    assert job_board["jobs"][0]["folder_name"] == new_name
    assert job_board["jobs"][0]["folderName"] == new_name
    assert job_board["jobs"][0]["jobNumber"] == "456"
    assert job_board["jobs"][0]["label"] == "keep"
    delivery_schedule = _read_json(root / ".metadata" / "delivery_schedule.json")
    assert delivery_schedule["items"][0]["folderName"] == new_name
    assert delivery_schedule["items"][0]["title"] == "Delivery for 456 - NEW CUSTOMER"

    gate = _read_json(root / new_name / ".metadata" / "deployment_gate.json")
    assert gate["jobFolderName"] == new_name
    assert gate["deployed"] is True
    assert gate["parseReady"] is True

    cache = _read_json(root / new_name / ".metadata" / "cache_static.json")
    assert cache["jobInfo"]["folderName"] == new_name
    assert cache["jobInfo"]["jobNumber"] == "456"
    assert cache["jobInfo"]["jobName"] == "NEW CUSTOMER"
    assert cache["cncJob"]["folderName"] == new_name

    sidecar = _read_json(root / new_name / "CNC" / ".metadata" / "123 - Maple.json")
    assert sidecar["jobFolderName"] == new_name
    assert sidecar["jobNumber"] == "456"
    assert sidecar["jobName"] == "456 - NEW CUSTOMER"
    assert sidecar["pages"][0]["sheetSummary"]["job"] == "456 - NEW CUSTOMER"


def test_rename_ready_job_rejects_duplicate_destination(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "123 - OLD").mkdir(parents=True)
    (root / "456 - NEW").mkdir(parents=True)

    try:
        rename_ready_job(
            root, "123 - OLD", "456 - NEW", rename_history_file=tmp_path / "rename_history.json"
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("Expected duplicate destination to be rejected")


def test_rename_ready_job_renames_orphaned_derived_files(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "123 - OLD CUSTOMER"
    new_name = "456 - NEW CUSTOMER"
    job = root / old_name
    dark_mode = job / "DARK MODE"
    dark_mode.mkdir(parents=True)
    (dark_mode / "123 - ASSEMBLY SHEETS.pdf").write_bytes(b"pdf")
    (dark_mode / "123 - DELIVERY SHEETS.pdf").write_bytes(b"pdf")
    # Already-correct-prefix file must be left alone (no rename attempted, no error).
    (dark_mode / "456 - ALREADY CORRECT.pdf").write_bytes(b"pdf")
    # A tablet-authored tracker file must never be touched even if its name happens
    # to start with the old job number.
    tracker_dir = job / "CNC" / ".tracker"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "123 - tablet-07.json").write_text("{}", encoding="utf-8")

    result = rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    new_dark_mode = root / new_name / "DARK MODE"
    assert (new_dark_mode / "456 - ASSEMBLY SHEETS.pdf").exists()
    assert (new_dark_mode / "456 - DELIVERY SHEETS.pdf").exists()
    assert (new_dark_mode / "456 - ALREADY CORRECT.pdf").exists()
    assert not (new_dark_mode / "123 - ASSEMBLY SHEETS.pdf").exists()

    new_tracker_dir = root / new_name / "CNC" / ".tracker"
    assert (new_tracker_dir / "123 - tablet-07.json").exists()

    renamed_names = {p.name for p in result.renamed_derived_files}
    assert renamed_names == {"456 - ASSEMBLY SHEETS.pdf", "456 - DELIVERY SHEETS.pdf"}


def test_rename_ready_job_does_not_corrupt_unrelated_job_records(tmp_path):
    root = tmp_path / "Ready Jobs"
    old_name = "502 - HARTFORD MCCASLIN REFACE"
    new_name = "649 - HARTFORD MCCASLIN REFACE"
    (root / old_name).mkdir(parents=True)

    _write_json(
        root / "job_board.json",
        {
            "jobs": [
                {
                    "folder_name": old_name,
                    "job_number": "502",
                    "job_name": "HARTFORD MCCASLIN REFACE",
                    "construction_method": "FACE-FRAME",
                },
                {
                    "folder_name": "999 - UNRELATED JOB",
                    "job_number": "999",
                    "job_name": "UNRELATED JOB",
                    # This unrelated job's construction_method happens to already contain
                    # the renamed job's old name as a value (a real, separate, pre-existing
                    # Hours Tracker data bug) - a blanket substring replace would corrupt it
                    # even though this record belongs to a completely different job.
                    "construction_method": old_name,
                },
            ]
        },
    )

    rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    job_board = _read_json(root / "job_board.json")
    renamed_entry = job_board["jobs"][0]
    unrelated_entry = job_board["jobs"][1]

    assert renamed_entry["folder_name"] == new_name
    assert renamed_entry["job_number"] == "649"
    assert unrelated_entry["folder_name"] == "999 - UNRELATED JOB"
    assert unrelated_entry["job_number"] == "999"
    assert unrelated_entry["construction_method"] == old_name  # untouched - not this job's record


def test_rename_ready_job_falls_through_empty_folder_name_to_job_number(tmp_path):
    # A record with a present-but-empty/null folder_name must not be treated as a definite
    # mismatch on that tier alone - it should fall through to the job_number tier, which can
    # still positively identify it as the renamed job's own record.
    root = tmp_path / "Ready Jobs"
    old_name = "502 - HARTFORD MCCASLIN REFACE"
    new_name = "649 - HARTFORD MCCASLIN REFACE"
    (root / old_name).mkdir(parents=True)

    _write_json(
        root / "job_board.json",
        {
            "jobs": [
                {
                    "folder_name": None,
                    "job_number": "502",
                    "job_name": "HARTFORD MCCASLIN REFACE",
                    "construction_method": "FACE-FRAME",
                },
            ]
        },
    )

    rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    job_board = _read_json(root / "job_board.json")
    entry = job_board["jobs"][0]

    assert entry["job_number"] == "649"


def test_rename_ready_job_uses_job_number_when_folder_name_is_stale(tmp_path):
    # A record whose folder_name is present but WRONG (e.g. drifted out of sync with Hours
    # Tracker's own copy) must still be recognized as the renamed job's own record when its
    # job_number correctly matches - job_number is unique per job and more authoritative than
    # a mirrored display string. Getting this wrong would silently skip rewriting the job's own
    # record, entrenching stale data instead of self-healing on the next rename.
    root = tmp_path / "Ready Jobs"
    old_name = "502 - HARTFORD MCCASLIN REFACE"
    new_name = "649 - HARTFORD MCCASLIN REFACE"
    (root / old_name).mkdir(parents=True)

    _write_json(
        root / "job_board.json",
        {
            "jobs": [
                {
                    "folder_name": "502 - SOME STALE MIRRORED NAME",
                    "job_number": "502",
                    "job_name": "HARTFORD MCCASLIN REFACE",
                    "construction_method": "FACE-FRAME",
                },
            ]
        },
    )

    rename_ready_job(
        root, old_name, new_name, archive_root=None, rename_history_file=tmp_path / "rename_history.json"
    )

    job_board = _read_json(root / "job_board.json")
    entry = job_board["jobs"][0]

    assert entry["job_number"] == "649"
