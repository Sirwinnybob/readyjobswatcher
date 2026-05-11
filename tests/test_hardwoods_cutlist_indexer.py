import json
import os

import ready_jobs_watcher.hardwoods_cutlist_indexer as indexer
import ready_jobs_watcher.reindex_hardwoods_cutlists as reindex_cli


class _FakePage:
    def __init__(self, *, words=None, text=""):
        self._words = words or []
        self._text = text

    def get_text(self, mode="text", *_args, **_kwargs):
        if mode == "words":
            return self._words
        return self._text


class _FakeDoc:
    def __init__(self, pages):
        self._pages = pages
        self.page_count = len(self._pages)

    def __getitem__(self, idx):
        return self._pages[idx]

    def close(self):
        return None


def _w(x, y, text):
    return (float(x), float(y), float(x) + 8.0, float(y) + 8.0, text, 0, 0, 0)


def _std_header(y=160.0):
    return [
        _w(80, y, "Qty"),
        _w(108, y, "|"),
        _w(114, y, "Description"),
        _w(166, y, "|"),
        _w(278, y, "|"),
        _w(284, y, "Width"),
        _w(322, y, "*"),
        _w(336, y, "Length"),
        _w(369, y, "|"),
        _w(468, y, "|"),
        _w(474, y, "Cabinet"),
        _w(510, y, "(Qty)"),
        _w(534, y, "|"),
    ]


def _std_row(y, qty, desc, width, length, cab_text):
    words = [
        _w(86, y, str(qty)),
        _w(108, y, "|"),
        _w(114, y, desc.split()[0]),
    ]
    if len(desc.split()) > 1:
        words.append(_w(142, y, " ".join(desc.split()[1:])))
    words += [
        _w(166, y, "|"),
        _w(278, y, "|"),
        _w(286, y, str(width)),
        _w(322, y, "*"),
        _w(336, y, str(length)),
        _w(369, y, "|"),
        _w(468, y, "|"),
    ]
    for i, tok in enumerate(cab_text.split()):
        words.append(_w(500 + i * 12, y, tok))
    words.append(_w(534, y, "|"))
    return words


def _door_header(y=200.0):
    return [
        _w(75, y, "Qty"),
        _w(147, y, "|"),
        _w(153, y, "Width"),
        _w(186, y, "*"),
        _w(196, y, "Height"),
        _w(228, y, "|"),
        _w(289, y, "|"),
        _w(294, y, "Type"),
        _w(318, y, "|"),
        _w(335, y, "|"),
        _w(340, y, "Hinge"),
        _w(485, y, "|"),
        _w(490, y, "Cab"),
        _w(510, y, "(Qty)"),
        _w(534, y, "|"),
    ]


def _door_row(y, qty, width, height, cab_text):
    words = [
        _w(83, y, str(qty)),
        _w(145, y, "|"),
        _w(150, y, str(width)),
        _w(186, y, "*"),
        _w(196, y, str(height)),
        _w(228, y, "|"),
        _w(289, y, "|"),
        _w(299, y, "DF"),
        _w(318, y, "|"),
        _w(335, y, "|"),
        _w(347, y, "N"),
        _w(485, y, "|"),
    ]
    for i, tok in enumerate(cab_text.split()):
        words.append(_w(500 + i * 12, y, tok))
    words.append(_w(534, y, "|"))
    return words


def _load_output(job_dir: str):
    out_path = os.path.join(job_dir, ".metadata", "hardwoods", "cutlist_index.json")
    with open(out_path, "r", encoding="utf-8") as f:
        return out_path, json.load(f)


def _load_revisions(job_dir: str):
    out_path = os.path.join(job_dir, ".metadata", "hardwoods", "cutlist_revisions.json")
    with open(out_path, "r", encoding="utf-8") as f:
        return out_path, json.load(f)


def test_new_template_rows_include_material_field_and_values(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(182, 2, "Bottom Rail", "4.75", "54", "15, 16")
    page_words += _std_row(201, 1, "Top Rail", "3", "56.5", "30")

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert len(rows) == 2
    assert rows[0]["material"] == "3/4 Maple"
    assert rows[0]["width"] == "4.75"
    assert rows[0]["length"] == "54"
    assert rows[0]["cabinets"] == ["15", "16"]
    assert rows[0]["rowId"].startswith(f"{indexer.DOC_TYPE_FACE_FRAME}:1:0:")


def test_multiple_material_sections_on_same_page_all_rows_captured(tmp_path, monkeypatch):
    job_dir = tmp_path / "300 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "300 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    # Section 1
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Paint"), _w(192, 130, "Grade"), _w(226, 130, "Wood'")]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Part A", "3.0", "24.0", "10 (1)")
    page_words += [_w(75, 210, "Totals"), _w(250, 210, "Width"), _w(320, 210, "Length"), _w(390, 210, "Rips")]
    # Section 2 (same page)
    page_words += [_w(74, 260, "Material:"), _w(124, 260, "'3/4"), _w(170, 260, "Solid"), _w(210, 260, "Alder'")]
    page_words += _std_header(290)
    page_words += _std_row(314, 2, "Part B", "2.0", "30.0", "20 (2)")

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert len(rows) == 2
    assert rows[0]["material"] == "3/4 Paint Grade Wood"
    assert rows[1]["material"] == "3/4 Solid Alder"


def test_door_list_rows_use_door_type_as_material_with_page_carry(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    door_list = job_dir / "998 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    page1_words = []
    page1_words += [_w(74, 142, "Door"), _w(111, 142, "Type:"), _w(151, 142, "'Shaker'")]
    page1_words += _door_header(202)
    page1_words += _door_row(242, 1, "23.875", "13.5625", "31 (2), 32 (2)")

    page2_words = []
    page2_words += _door_header(202)
    page2_words += _door_row(242, 2, "20.375", "12.125", "4 (2)")

    doc_map = {str(door_list): _FakeDoc([_FakePage(words=page1_words), _FakePage(words=page2_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"]

    assert len(rows) == 2
    assert rows[0]["material"] == "Shaker"
    assert rows[0]["description"] == "Shaker"
    assert rows[1]["material"] == "Shaker"


def test_totals_material_and_spillover_source_pages_continuity(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    nailer = job_dir / "998 - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    page1_words = []
    page1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Prefinished'")]
    page1_words += _std_header(160)
    page1_words += _std_row(184, 1, "Nailer", "2.25", "75.258", "18")
    page1_words += [_w(75, 400, "Totals"), _w(250, 400, "Width"), _w(320, 400, "Length"), _w(390, 400, "Rips")]
    page1_words += [_w(250, 420, "2.25"), _w(320, 420, "75.258"), _w(390, 420, "1")]

    page2_words = []
    page2_words += [_w(250, 70, "3"), _w(320, 70, "99"), _w(390, 70, "2")]

    doc_map = {str(nailer): _FakeDoc([_FakePage(words=page1_words), _FakePage(words=page2_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    totals = docs[indexer.DOC_TYPE_NAILER]["totals"]

    assert len(totals) == 1
    assert totals[0]["material"] == "3/4 Prefinished"
    assert totals[0]["widthValues"] == ["2.25", "3"]
    assert totals[0]["lengthValues"] == ["75.258", "99"]
    assert totals[0]["ripsValues"] == ["1", "2"]
    assert totals[0]["sourcePages"] == [1, 2]


def test_old_template_hard_fail_excludes_doc_and_removes_stale_index(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    metadata_dir = job_dir / ".metadata" / "hardwoods"
    metadata_dir.mkdir(parents=True)
    stale = metadata_dir / "cutlist_index.json"
    stale.write_text('{"documents":[]}', encoding="utf-8")

    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    # Old-style/non-template words: no pipe-delimited header.
    old_words = [
        _w(80, 100, "Face"),
        _w(120, 100, "Frame"),
        _w(80, 140, "2"),
        _w(120, 140, "Bottom"),
        _w(180, 140, "Rail"),
    ]
    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=old_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    # No valid docs -> stale index removed.
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert not stale.exists()


def test_light_preferred_over_dark_fallback(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    dark_dir = job_dir / "DARK MODE"
    dark_dir.mkdir()

    light = job_dir / "998 - Nailer Cut List.pdf"
    dark = dark_dir / "998 - Nailer Cut List.pdf"
    light.write_text("placeholder", encoding="utf-8")
    dark.write_text("placeholder", encoding="utf-8")

    light_words = [_w(74, 130, "Material:"), _w(124, 130, "'Light'"), *_std_header(160), *_std_row(184, 2, "Nailer", "2.25", "58.019", "30 (2)")]
    dark_words = [_w(74, 130, "Material:"), _w(124, 130, "'Dark'"), *_std_header(160), *_std_row(184, 1, "Nailer", "2.25", "75.258", "18")]

    doc_map = {
        str(light): _FakeDoc([_FakePage(words=light_words)]),
        str(dark): _FakeDoc([_FakePage(words=dark_words)]),
    }
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_NAILER]["rows"]
    assert rows[0]["qty"] == 2
    assert rows[0]["material"] == "Light"


def test_cutlist_spillover_without_repeated_header_uses_previous_table_geometry(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    # Page 1: material + header + one row.
    page1_words = []
    page1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Paint"), _w(192, 130, "Grade"), _w(230, 130, "Wood'")]
    page1_words += _std_header(160)
    page1_words += _std_row(184, 2, "Bottom Rail", "4.75", "54", "15, 16")

    # Page 2: continuation rows only, no repeated header.
    page2_words = []
    page2_words += _std_row(92, 1, "Left Stile", "3.25", "34.5", "15")
    page2_words += _std_row(112, 1, "Right Stile", "3.25", "34.5", "16")

    doc_map = {
        str(face_frame): _FakeDoc([_FakePage(words=page1_words), _FakePage(words=page2_words)])
    }
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert len(rows) == 3
    assert [r["page"] for r in rows] == [1, 2, 2]
    assert all(r["material"] == "3/4 Paint Grade Wood" for r in rows)


def test_replacement_reorder_preserves_row_ids_by_material_length_width(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Part A", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Part B", "3", "12", "2")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Part B", "3", "12", "2")
    run2_words += _std_row(202, 1, "Part A", "2.5", "10", "1")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        path_docs = docs[str(path)]
        return path_docs.pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload1 = _load_output(str(job_dir))
    rows1 = payload1["documents"][0]["rows"]
    row_id_by_dims = {(r["material"], r["length"], r["width"]): r["rowId"] for r in rows1}

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload2 = _load_output(str(job_dir))
    rows2 = payload2["documents"][0]["rows"]
    row_id_by_dims2 = {(r["material"], r["length"], r["width"]): r["rowId"] for r in rows2}

    assert row_id_by_dims2 == row_id_by_dims


def test_replacement_removed_rows_drop_and_added_rows_get_new_ids(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Part A", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Part B", "3", "12", "2")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Part A", "2.5", "10", "1")
    run2_words += _std_row(202, 1, "Part C", "4", "20", "3")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        path_docs = docs[str(path)]
        return path_docs.pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload1 = _load_output(str(job_dir))
    rows1 = payload1["documents"][0]["rows"]
    part_a_old = next(r for r in rows1 if r["description"] == "Part A")
    part_b_old = next(r for r in rows1 if r["description"] == "Part B")

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload2 = _load_output(str(job_dir))
    rows2 = payload2["documents"][0]["rows"]
    part_a_new = next(r for r in rows2 if r["description"] == "Part A")
    part_c_new = next(r for r in rows2 if r["description"] == "Part C")

    assert part_a_new["rowId"] == part_a_old["rowId"]
    assert part_c_new["rowId"] != part_b_old["rowId"]
    assert part_b_old["rowId"] not in {r["rowId"] for r in rows2}


def test_duplicate_match_uses_tracker_done_priority_then_order(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Alpha", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Beta", "2.5", "10", "2")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Beta", "2.5", "10", "2")
    run2_words += _std_row(202, 1, "Alpha", "2.5", "10", "1")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        path_docs = docs[str(path)]
        return path_docs.pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload1 = _load_output(str(job_dir))
    rows1 = payload1["documents"][0]["rows"]
    alpha_id = next(r["rowId"] for r in rows1 if r["description"] == "Alpha")
    beta_id = next(r["rowId"] for r in rows1 if r["description"] == "Beta")

    tracker_dir = job_dir / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_payload = {
        "tabletId": "tablet-a",
        "actions": [
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "rowId": alpha_id,
                "action": "set_done_count",
                "value": 1,
                "timestamp": "2026-05-07T10:00:00Z",
            },
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "rowId": beta_id,
                "action": "set_done_count",
                "value": 3,
                "timestamp": "2026-05-07T10:00:01Z",
            },
        ],
    }
    (tracker_dir / "tablet-a.json").write_text(json.dumps(tracker_payload), encoding="utf-8")

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload2 = _load_output(str(job_dir))
    rows2 = payload2["documents"][0]["rows"]
    assert rows2[0]["description"] == "Beta"
    assert rows2[0]["rowId"] == beta_id
    assert rows2[1]["rowId"] == alpha_id


def test_replacement_does_not_transfer_row_ids_across_doc_types(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    nailer = job_dir / "998 - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    # Pre-seed a prior FACE_FRAME row with identical dimensions/material.
    metadata_dir = job_dir / ".metadata" / "hardwoods"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    seeded = {
        "generatedAt": "2026-05-07T00:00:00Z",
        "documents": [
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "pdfFilename": "998 - Face Frame Cut List.pdf",
                "pageCount": 1,
                "rows": [
                    {
                        "rowId": "FACE_FRAME_CUT_LIST:1:0:seeded",
                        "page": 1,
                        "rowOrdinal": 0,
                        "qty": 1,
                        "description": "Seed",
                        "width": "2.5",
                        "length": "10",
                        "cabinets": ["1"],
                        "rawCabinetText": "1",
                        "material": "3/4 Maple",
                    }
                ],
                "totals": [],
            }
        ],
    }
    (metadata_dir / "cutlist_index.json").write_text(json.dumps(seeded), encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(182, 1, "Nailer", "2.5", "10", "1")
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_NAILER]["rows"]
    assert rows[0]["rowId"] != "FACE_FRAME_CUT_LIST:1:0:seeded"


def test_door_cut_list_totals_lengths_export_in_feet(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    door_cut = job_dir / "998 - Door Cut List.pdf"
    door_cut.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Door Rail", "2.25", "30", "18")
    page_words += [_w(75, 400, "Totals"), _w(250, 400, "Width"), _w(320, 400, "Length"), _w(390, 400, "Rips")]
    page_words += [_w(250, 420, "2.25"), _w(320, 420, "120"), _w(390, 420, "1")]
    page_words += [_w(250, 440, "3"), _w(320, 440, "30"), _w(390, 440, "2")]

    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    totals = docs[indexer.DOC_TYPE_DOOR_CUT]["totals"]

    assert len(totals) == 1
    assert totals[0]["lengthValues"] == ["10", "2.5"]


def test_non_door_totals_lengths_remain_unmodified(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Face Rail", "2.25", "30", "18")
    page_words += [_w(75, 400, "Totals"), _w(250, 400, "Width"), _w(320, 400, "Length"), _w(390, 400, "Rips")]
    page_words += [_w(250, 420, "2.25"), _w(320, 420, "120"), _w(390, 420, "1")]

    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    totals = docs[indexer.DOC_TYPE_FACE_FRAME]["totals"]

    assert len(totals) == 1
    assert totals[0]["lengthValues"] == ["120"]


def test_reindex_cli_preserves_row_ids_and_tracker_priority(tmp_path, monkeypatch):
    root_dir = tmp_path / "ready-jobs"
    root_dir.mkdir()
    job_dir = root_dir / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Alpha", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Beta", "2.5", "10", "2")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Beta", "2.5", "10", "2")
    run2_words += _std_row(202, 1, "Alpha", "2.5", "10", "1")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        path_docs = docs[str(path)]
        return path_docs.pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload1 = _load_output(str(job_dir))
    rows1 = payload1["documents"][0]["rows"]
    alpha_id = next(r["rowId"] for r in rows1 if r["description"] == "Alpha")
    beta_id = next(r["rowId"] for r in rows1 if r["description"] == "Beta")

    tracker_dir = job_dir / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_payload = {
        "tabletId": "tablet-a",
        "actions": [
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "rowId": alpha_id,
                "action": "set_done_count",
                "value": 1,
                "timestamp": "2026-05-07T10:00:00Z",
            },
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "rowId": beta_id,
                "action": "set_done_count",
                "value": 3,
                "timestamp": "2026-05-07T10:00:01Z",
            },
        ],
    }
    (tracker_dir / "tablet-a.json").write_text(json.dumps(tracker_payload), encoding="utf-8")

    summary = reindex_cli.run_reindex(str(root_dir), dry_run=False, jobs=[job_dir.name])
    assert summary.jobsFailed == 0
    assert summary.jobsSucceeded == 1
    assert summary.results[0].jobFolder == job_dir.name
    assert summary.results[0].status == "success"

    _, payload2 = _load_output(str(job_dir))
    rows2 = payload2["documents"][0]["rows"]
    assert rows2[0]["description"] == "Beta"
    assert rows2[0]["rowId"] == beta_id
    assert rows2[1]["rowId"] == alpha_id


def test_revision_baseline_writes_r1_snapshot(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(182, 1, "Part A", "2.5", "10", "1")
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=page_words)]))

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, revisions = _load_revisions(str(job_dir))
    assert revisions["currentRevision"] == 1
    assert len(revisions["revisions"]) == 1
    assert revisions["revisions"][0]["revision"] == 1
    assert revisions["revisions"][0]["kind"] == "SNAPSHOT"
    assert revisions["revisions"][0]["added"] == []
    assert revisions["revisions"][0]["removed"] == []
    assert revisions["revisions"][0]["modified"] == []


def test_revision_diff_classifies_added_removed_modified(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Alpha", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Remove Me", "3", "12", "2")
    run1_words += _std_row(222, 1, "Resize Me", "4", "20", "3")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Alpha", "2.5", "10", "1")
    run2_words += _std_row(202, 1, "Resize Me", "4", "22", "3")
    run2_words += _std_row(222, 1, "Added", "5", "40", "4")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        return docs[str(path)].pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True

    _, revisions = _load_revisions(str(job_dir))
    assert revisions["currentRevision"] == 2
    latest = revisions["revisions"][-1]
    assert latest["kind"] == "DIFF"
    assert len(latest["added"]) == 1
    assert len(latest["removed"]) == 1
    assert len(latest["modified"]) == 1
    assert latest["modified"][0]["changedFields"] == ["length"]


def test_revision_reorder_only_does_not_increment_revision(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Part A", "2.5", "10", "1")
    run1_words += _std_row(202, 1, "Part B", "3", "12", "2")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Part B", "3", "12", "2")
    run2_words += _std_row(202, 1, "Part A", "2.5", "10", "1")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        return docs[str(path)].pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True

    _, revisions = _load_revisions(str(job_dir))
    assert revisions["currentRevision"] == 1
    assert len(revisions["revisions"]) == 1


def test_modified_completed_row_sets_changed_pending_recut(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    run1_words = []
    run1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run1_words += _std_header(160)
    run1_words += _std_row(182, 1, "Resize Me", "4", "20", "3")

    run2_words = []
    run2_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    run2_words += _std_header(160)
    run2_words += _std_row(182, 1, "Resize Me", "4", "22", "3")

    docs = {
        str(face_frame): [
            _FakeDoc([_FakePage(words=run1_words)]),
            _FakeDoc([_FakePage(words=run2_words)]),
        ]
    }

    def _open(path):
        return docs[str(path)].pop(0)

    monkeypatch.setattr(indexer.fitz, "open", _open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, first_payload = _load_output(str(job_dir))
    old_row_id = first_payload["documents"][0]["rows"][0]["rowId"]
    tracker_dir = job_dir / ".metadata" / "hardwoods" / ".tracker"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_payload = {
        "tabletId": "tablet-a",
        "actions": [
            {
                "docType": indexer.DOC_TYPE_FACE_FRAME,
                "rowId": old_row_id,
                "action": "set_done_count",
                "value": 1,
                "timestamp": "2026-05-07T10:00:00Z",
            },
        ],
    }
    (tracker_dir / "tablet-a.json").write_text(json.dumps(tracker_payload), encoding="utf-8")

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, revisions = _load_revisions(str(job_dir))
    latest = revisions["revisions"][-1]
    assert latest["kind"] == "DIFF"
    assert len(latest["modified"]) == 1
    state_rows = revisions["currentRowStates"]
    assert len(state_rows) == 1
    assert state_rows[0]["latestRevision"] == revisions["currentRevision"]
    assert state_rows[0]["changedPendingRecut"] is True
