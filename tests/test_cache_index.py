import json

import pytest
from pathlib import Path

from ready_jobs_watcher.metadata_cache import (
    _compute_progress_summary,
    _validate_cache_index,
    generate_cache_index,
    update_all_jobs_cache,
)


def test_progress_summary_empty_tracker(tmp_path):
    job = tmp_path / "123 - Test"
    meta = job / ".metadata"
    meta.mkdir(parents=True)
    cnc_tracker = job / "CNC" / ".tracker"
    cnc_tracker.mkdir(parents=True)
    (cnc_tracker / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": []}),
        encoding="utf-8",
    )
    static_data = {
        "jobInfo": {"folderName": "123 - Test"},
        "cncJob": {
            "materials": [
                {
                    "materialName": "FRAME",
                    "pageCount": 5,
                    "pdfFilename": "123 - FRAME.pdf",
                }
            ]
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    assert result["cnc"]["totalSheets"] == 5
    assert result["cnc"]["done"] == 0
    assert result["cnc"]["bad"] == 0
    assert result["cnc"]["materials"][0]["done"] == 0
    assert result["hasDeliverySheet"] is False
    assert result["has3DAssets"] is False


def test_progress_summary_cnc_single_done(tmp_path):
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    actions = [
        {
            "file": "123 - FRAME.pdf",
            "page": 1,
            "action": "complete",
            "timestamp": "1",
        }
    ]
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": actions}), encoding="utf-8"
    )
    static_data = {
        "cncJob": {
            "materials": [
                {
                    "materialName": "FRAME",
                    "pageCount": 5,
                    "pdfFilename": "123 - FRAME.pdf",
                }
            ]
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    assert result["cnc"]["done"] == 1
    assert result["cnc"]["materials"][0]["done"] == 1
    assert result["cnc"]["materials"][0]["bad"] == 0


def test_progress_summary_cnc_multi_material(tmp_path):
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    actions = [
        {
            "file": "123 - FRAME.pdf",
            "page": 1,
            "action": "bad_part",
            "part": 1,
            "timestamp": "1",
        },
        {
            "file": "123 - FRAME.pdf",
            "page": 2,
            "action": "complete",
            "timestamp": "2",
        },
        {
            "file": "123 - DOOR.pdf",
            "page": 1,
            "action": "skip",
            "timestamp": "3",
        },
    ]
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": actions}), encoding="utf-8"
    )
    static_data = {
        "cncJob": {
            "materials": [
                {
                    "materialName": "FRAME",
                    "pageCount": 5,
                    "pdfFilename": "123 - FRAME.pdf",
                },
                {
                    "materialName": "DOOR",
                    "pageCount": 3,
                    "pdfFilename": "123 - DOOR.pdf",
                },
            ]
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": {"pdfFilename": "del.pdf"}},
    }
    result = _compute_progress_summary(job, static_data)
    cnc = result["cnc"]
    assert cnc["totalSheets"] == 8
    assert cnc["done"] == 1
    assert cnc["bad"] == 1
    assert cnc["skipped"] == 1
    assert result["hasDeliverySheet"] is True
    frame = [m for m in cnc["materials"] if m["materialName"] == "FRAME"][0]
    assert frame["done"] == 1 and frame["bad"] == 1
    door = [m for m in cnc["materials"] if m["materialName"] == "DOOR"][0]
    assert door["skipped"] == 1


def test_progress_summary_unskip_reverts(tmp_path):
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    actions = [
        {"file": "123 - FRAME.pdf", "page": 1, "action": "skip", "timestamp": "1"},
        {
            "file": "123 - FRAME.pdf",
            "page": 1,
            "action": "unskip",
            "timestamp": "2",
        },
    ]
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": actions}), encoding="utf-8"
    )
    static_data = {
        "cncJob": {
            "materials": [
                {
                    "materialName": "FRAME",
                    "pageCount": 5,
                    "pdfFilename": "123 - FRAME.pdf",
                }
            ]
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    cnc = result["cnc"]
    assert cnc["skipped"] == 0
    assert cnc["done"] == 1


def test_generate_cache_index_writes_file(tmp_path):
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": []}), encoding="utf-8"
    )
    static_data = {
        "jobInfo": {
            "folderName": "123 - Test",
            "jobNumber": "123",
            "jobName": "Test",
            "hiddenFromProduction": False,
            "lineupPosition": 1,
        },
        "cncJob": {
            "materials": [
                {
                    "materialName": "FRAME",
                    "pageCount": 3,
                    "pdfFilename": "123 - FRAME.pdf",
                }
            ]
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = generate_cache_index(job, static_data)
    index_file = job / ".metadata" / "cache_index.json"
    assert index_file.exists()
    assert result["jobInfo"]["folderName"] == "123 - Test"
    assert "progressSummary" in result
    assert result["progressSummary"]["cnc"]["totalSheets"] == 3


def test_generate_cache_index_skip_if_unchanged(tmp_path):
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"tabletId": "consolidated", "actions": []}), encoding="utf-8"
    )
    static_data = {
        "jobInfo": {"folderName": "123 - Test"},
        "cncJob": {"materials": []},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    generate_cache_index(job, static_data)
    mtime1 = (job / ".metadata" / "cache_index.json").stat().st_mtime_ns
    generate_cache_index(job, static_data)
    mtime2 = (job / ".metadata" / "cache_index.json").stat().st_mtime_ns
    assert mtime1 == mtime2, "identical payload should not rewrite"


def test_update_all_jobs_cache_writes_index(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / "CNC").mkdir(parents=True)
    gate = {"deployed": True, "parseReady": True, "hiddenFromProduction": False}
    (job / ".metadata" / "deployment_gate.json").write_text(
        json.dumps(gate), encoding="utf-8"
    )
    result = update_all_jobs_cache(tmp_path, consolidate_trackers=False, archive=False)
    assert (job / ".metadata" / "cache_index.json").exists()
    assert result["rebuilt"] == 1


def test_hardwood_progress_counts(tmp_path):
    """_compute_progress_summary returns hardwood progress from consolidated tracker."""
    job = tmp_path / "123 - Test"
    hw_tracker = job / ".metadata" / "hardwoods" / ".tracker"
    hw_tracker.mkdir(parents=True)
    actions = [
        {"docType": "FACE_FRAME_CUT_LIST", "rowId": "r1", "action": "set_done_count", "value": 1, "timestamp": "1"},
        {"docType": "FACE_FRAME_CUT_LIST", "rowId": "r2", "action": "set_bad_count", "value": 1, "timestamp": "2"},
        {"docType": "NAILER_CUT_LIST", "rowId": "r3", "action": "set_skipped", "timestamp": "3"},
    ]
    (hw_tracker / "consolidated.json").write_text(
        json.dumps({"actions": actions}), encoding="utf-8"
    )
    static_data = {
        "hardwoodJob": {
            "index": {
                "documents": [
                    {"docType": "FACE_FRAME_CUT_LIST", "rows": [{"rowId": "r1"}, {"rowId": "r2"}]},
                    {"docType": "NAILER_CUT_LIST", "rows": [{"rowId": "r3"}]},
                ]
            }
        },
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    hw = result["hardwoods"]
    assert hw["totalPieces"] == 3
    assert hw["donePieces"] == 1
    assert hw["badPieces"] == 1
    assert hw["skippedPieces"] == 1
    assert hw["docTypes"][0]["docType"] == "FACE_FRAME_CUT_LIST"
    assert hw["docTypes"][0]["done"] == 1


def test_missing_consolidated_json(tmp_path):
    """Missing consolidated.json yields zero progress, no crash."""
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    static_data = {
        "cncJob": {"materials": [
            {"materialName": "FRAME", "pageCount": 5, "pdfFilename": "123 - FRAME.pdf"}
        ]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    assert result["cnc"]["done"] == 0
    assert result["cnc"]["bad"] == 0
    assert result["cnc"]["totalSheets"] == 5


def test_corrupt_consolidated_json(tmp_path):
    """Corrupt consolidated.json yields zero progress, no crash."""
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        "not valid json", encoding="utf-8"
    )
    static_data = {
        "cncJob": {"materials": [
            {"materialName": "FRAME", "pageCount": 5, "pdfFilename": "123 - FRAME.pdf"}
        ]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    assert result["cnc"]["done"] == 0


def test_non_dict_actions_skipped(tmp_path):
    """Non-dict items in actions array are safely skipped."""
    job = tmp_path / "123 - Test"
    (job / "CNC" / ".tracker").mkdir(parents=True)
    (job / ".metadata").mkdir()
    actions = [
        "this is a string, not a dict",
        ["a", "list"],
        None,
        {"file": "123 - FRAME.pdf", "page": 1, "action": "complete", "timestamp": "1"},
    ]
    (job / "CNC" / ".tracker" / "consolidated.json").write_text(
        json.dumps({"actions": actions}), encoding="utf-8"
    )
    static_data = {
        "cncJob": {"materials": [
            {"materialName": "FRAME", "pageCount": 5, "pdfFilename": "123 - FRAME.pdf"}
        ]},
        "hasThreeDAssets": False,
        "pdfCatalog": {"deliverySheet": None},
    }
    result = _compute_progress_summary(job, static_data)
    assert result["cnc"]["done"] == 1  # only the valid dict action counts


def test_validate_cache_index_missing_file(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    assert _validate_cache_index(job) is False


def test_validate_cache_index_malformed_json(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / ".metadata" / "cache_index.json").write_text("not json", encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_empty_json(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    (job / ".metadata" / "cache_index.json").write_text("{}", encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_missing_progress(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"jobInfo": {"folderName": "123 - Test"}}
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_missing_jobInfo(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False}}
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_null_folderName(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {"jobInfo": {"folderName": None}, "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False}}
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_valid(tmp_path):
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": "123 - Test"},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is True


def test_validate_cache_index_empty_folderName(tmp_path):
    """folderName is an empty string — should fail."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": ""},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_folderName_not_string(tmp_path):
    """folderName is a number, not a string — should fail."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": 123},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_extra_keys(tmp_path):
    """Extra keys beyond required ones — should still pass."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": "123 - Test", "extraField": "ignored"},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": True, "has3DAssets": True, "extraMetric": {}},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is True


def test_validate_cache_index_missing_one_progress_key(tmp_path):
    """progressSummary missing 'has3DAssets' key — should fail."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": "123 - Test"},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_jobInfo_not_dict(tmp_path):
    """jobInfo is a string, not a dict — should fail."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": "not a dict",
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False


def test_validate_cache_index_whitespace_folderName(tmp_path):
    """folderName is whitespace-only — should fail."""
    job = tmp_path / "123 - Test"
    (job / ".metadata").mkdir(parents=True)
    data = {
        "jobInfo": {"folderName": "   "},
        "progressSummary": {"cnc": {}, "hardwoods": {}, "hasDeliverySheet": False, "has3DAssets": False},
    }
    (job / ".metadata" / "cache_index.json").write_text(json.dumps(data), encoding="utf-8")
    assert _validate_cache_index(job) is False
