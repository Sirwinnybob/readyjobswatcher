import json

from ready_jobs_watcher.metadata_cache import (
    build_board_stock_rows,
    check_cache_needs_rebuild,
    generate_static_cache,
    refresh_single_job,
    update_all_jobs_cache,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_static_cache_reads_pgm_sidecars_and_hours_tracker_manual_board_stock(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "123 - Test Job"
    cnc = job / "CNC"
    cnc.mkdir(parents=True)
    pdf = cnc / "123 - Maple.pdf"
    pdf.write_bytes(b"not a real pdf")
    _write_json(job / ".metadata" / "deployment_gate.json", {"deployed": True, "hiddenFromProduction": False})
    _write_json(
        cnc / ".metadata" / "123 - Maple.json",
        {
            "jobNumber": "123",
            "jobName": "Test Job",
            "material": "Maple",
            "pdfFilename": "123 - Maple.pdf",
            "pages": [{"pageNumber": 1, "parts": [{"number": 7, "name": "Rail"}]}],
        },
    )
    _write_json(
        job / ".metadata" / "hardwoods" / "board_stock_manual.json",
        {"entries": [{"material": "Walnut", "width": "2.5", "totalFeet": 21, "category": "custom"}]},
    )

    cache = generate_static_cache(job, "123 - Test Job", lineup_position=4)

    assert cache["jobInfo"]["lineupPosition"] == 4
    assert cache["cncJob"]["materials"][0]["metadata"]["pages"][0]["parts"][0]["number"] == 7
    assert cache["cncJob"]["materials"][0]["pageCount"] == 1
    assert cache["boardStockRows"][0]["source"] == "MANUAL"
    assert (job / ".metadata" / "cache_static.json").exists()


def test_static_cache_reads_current_hours_tracker_admin_board_stock(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    _write_json(job / ".metadata" / "deployment_gate.json", {"deployed": True, "hiddenFromProduction": False})
    _write_json(
        job / ".metadata" / "admin" / "board_stock.json",
        {
            "schemaVersion": 1,
            "items": [
                {
                    "id": "manual-1",
                    "material": "Maple",
                    "name": "2.5",
                    "feet": 24,
                    "mode": "bd_ft",
                    "ripLength": 10,
                }
            ],
        },
    )

    cache = generate_static_cache(job, "123 - Test Job")

    assert cache["boardStockRows"] == [
        {
            "stableKey": "board_stock|Maple|2.5|MANUAL|manual-1",
            "material": "Maple",
            "width": "2.5",
            "normalizedWidth": 2.5,
            "source": "MANUAL",
            "sourceLabel": "MANUAL",
            "totalFeet": 24.0,
            "neededRips": 3,
            "manualCategory": "admin_board_stock",
            "manualSubtype": "bd_ft",
            "notes": "2.5",
        }
    ]


def test_board_stock_rows_use_detail_rows_when_totals_cross_a_material_page_break(tmp_path):
    index = {
        "documents": [
            {
                "docType": "DOOR_CUT_LIST",
                "rows": [
                    {
                        "material": "3/4 Solid White Oak Rift",
                        "width": "2.2813",
                        "length": "69.0625",
                        "qty": 2,
                    }
                ],
                # The current report can carry a previous material's totals onto
                # the page where the next material begins.  Those columns must
                # never become the source of a rip-list row.
                "totals": [
                    {
                        "material": "3/4 Solid White Oak Rift",
                        "widthValues": ["69.0625"],
                        "lengthValues": ["0.2593"],
                    }
                ],
            }
        ]
    }

    rows = build_board_stock_rows(tmp_path / "123 - Test Job", index)

    assert rows == [
        {
            "stableKey": "board_stock|3/4 Solid White Oak Rift|2.2813|DOOR",
            "material": "3/4 Solid White Oak Rift",
            "width": "2.2813",
            "normalizedWidth": 2.2813,
            "source": "DOOR",
            "sourceLabel": "DOOR",
            "totalFeet": 11.510416666666666,
            "neededRips": 2,
        }
    ]


def test_board_stock_rows_use_the_width_column_even_when_it_is_larger_than_length(tmp_path):
    index = {
        "documents": [
            {
                "docType": "DOOR_CUT_LIST",
                "rows": [
                    {
                        "material": "3/4 Solid White Oak Rift",
                        "width": "69.0625",
                        "length": "3.0625",
                        "qty": 1,
                    }
                ],
                "totals": [],
            }
        ]
    }

    rows = build_board_stock_rows(tmp_path / "123 - Test Job", index)

    assert rows[0]["normalizedWidth"] == 69.0625
    assert rows[0]["width"] == "69.0625"
    assert rows[0]["totalFeet"] == 3.0625 / 12.0


def test_hours_tracker_admin_board_stock_marks_cache_stale(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    cache_file = job / ".metadata" / "cache_static.json"
    stock = job / ".metadata" / "admin" / "board_stock.json"
    _write_json(cache_file, {"jobInfo": {}})
    _write_json(stock, {"items": []})
    stale_time = cache_file.stat().st_mtime - 1

    assert check_cache_needs_rebuild(job, stale_time) is True


def test_cnc_sidecar_update_marks_cache_stale(tmp_path):
    job = tmp_path / "Ready Jobs" / "123 - Test Job"
    cache_file = job / ".metadata" / "cache_static.json"
    sidecar = job / "CNC" / ".metadata" / "123 - Maple.json"
    _write_json(cache_file, {"jobInfo": {}})
    _write_json(sidecar, {"pages": []})
    stale_time = cache_file.stat().st_mtime - 1

    assert check_cache_needs_rebuild(job, stale_time) is True


def test_update_all_jobs_cache_respects_production_order_but_does_not_rewrite_it(tmp_path):
    root = tmp_path / "Ready Jobs"
    first = root / "123 - First"
    second = root / "456 - Second"
    for job in (first, second):
        _write_json(job / ".metadata" / "deployment_gate.json", {"deployed": True, "hiddenFromProduction": False})
    order = root / "production_order.json"
    _write_json(order, ["456 - Second", "123 - First"])
    before = order.read_text(encoding="utf-8")

    update_all_jobs_cache(root, consolidate_trackers=False, archive=False)

    assert json.loads((second / ".metadata" / "cache_static.json").read_text(encoding="utf-8"))["jobInfo"]["lineupPosition"] == 1
    assert json.loads((first / ".metadata" / "cache_static.json").read_text(encoding="utf-8"))["jobInfo"]["lineupPosition"] == 2
    assert order.read_text(encoding="utf-8") == before


def test_static_cache_accepts_base_job_number_for_lettered_job_cnc_sheets(tmp_path):
    # Split/lettered jobs (e.g. "530a") sometimes get CNC remake sheets exported
    # under the base numeric job number ("530 - 001 REMAKE - ...") instead of the
    # lettered folder number ("530a - ..."). Those must still be picked up.
    job = tmp_path / "Ready Jobs" / "530a - Test Job"
    cnc = job / "CNC"
    cnc.mkdir(parents=True)
    (cnc / "530a - Maple.pdf").write_bytes(b"not a real pdf")
    (cnc / "530 - 001 REMAKE - Maple.pdf").write_bytes(b"not a real pdf")
    _write_json(job / ".metadata" / "deployment_gate.json", {"deployed": True, "hiddenFromProduction": False})

    cache = generate_static_cache(job, "530a - Test Job")

    material_names = {m["materialName"] for m in cache["cncJob"]["materials"]}
    assert material_names == {"Maple", "001 REMAKE - Maple"}


def test_static_cache_still_rejects_unrelated_job_number_prefix(tmp_path):
    job = tmp_path / "Ready Jobs" / "530a - Test Job"
    cnc = job / "CNC"
    cnc.mkdir(parents=True)
    (cnc / "999 - Other Job Sheet.pdf").write_bytes(b"not a real pdf")
    _write_json(job / ".metadata" / "deployment_gate.json", {"deployed": True, "hiddenFromProduction": False})

    cache = generate_static_cache(job, "530a - Test Job")

    assert cache["cncJob"]["materials"] == []


def test_refresh_single_job_does_not_recreate_deleted_job_folder(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "123 - Deleted Job"
    root.mkdir()

    result = refresh_single_job(root, job, reason="deleted_pdf", archive_root=None)

    assert result["skipped"] == "missing_job"
    assert not job.exists()
