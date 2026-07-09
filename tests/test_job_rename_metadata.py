import json

from ready_jobs_watcher.job_rename import rename_ready_job


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

    result = rename_ready_job(root, old_name, new_name, archive_root=None)

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
        rename_ready_job(root, "123 - OLD", "456 - NEW")
    except FileExistsError:
        pass
    else:
        raise AssertionError("Expected duplicate destination to be rejected")
