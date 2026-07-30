import json

import pytest
from pathlib import Path

from ready_jobs_watcher.metadata_cache import _compute_progress_summary


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
