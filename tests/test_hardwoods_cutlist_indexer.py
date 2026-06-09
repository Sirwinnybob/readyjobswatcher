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


def _legacy_door_header(y=202.5):
    return [
        _w(75, y, "Qty"),
        _w(112, y, "|"),
        _w(130, y, "Width"),
        _w(168, y, "*"),
        _w(180, y, "Height"),
        _w(228, y, "|"),
        _w(289, y, "|"),
        _w(304, y, "Type"),
        _w(336, y, "|"),
        _w(352, y, "|"),
        _w(366, y, "Hinge"),
        _w(432, y, "|"),
        _w(448, y, "Cab"),
        _w(484, y, "(Qty)"),
        _w(534, y, "|"),
    ]


def _legacy_door_row(y, qty, width, height, door_type="DF", hinge="N", cab_text=""):
    words = [
        _w(83, y, str(qty)),
        _w(112, y, "|"),
        _w(126, y, str(width)),
        _w(168, y, "*"),
        _w(180, y, str(height)),
        _w(228, y, "|"),
        _w(289, y, "|"),
        _w(302, y, str(door_type)),
        _w(336, y, "|"),
        _w(352, y, "|"),
        _w(366, y, str(hinge)),
        _w(432, y, "|"),
        _w(446, y, "|"),
    ]
    if cab_text:
        for i, tok in enumerate(cab_text.split()):
            words.append(_w(460 + i * 12, y, tok))
        words.append(_w(534, y, "|"))
    else:
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


def test_section_marker_extracts_material_and_units_v2():
    rows_by_y = [
        (
            130.0,
            [
                {"x0": 74.0, "x1": 100.0, "y0": 130.0, "y1": 138.0, "text": "Material:", "upper": "MATERIAL:"},
                {"x0": 124.0, "x1": 138.0, "y0": 130.0, "y1": 138.0, "text": "'3/4", "upper": "'3/4"},
                {"x0": 160.0, "x1": 205.0, "y0": 130.0, "y1": 138.0, "text": "Maple'", "upper": "MAPLE'"},
                {"x0": 210.0, "x1": 214.0, "y0": 130.0, "y1": 138.0, "text": "|", "upper": "|"},
                {"x0": 220.0, "x1": 252.0, "y0": 130.0, "y1": 138.0, "text": "Units:", "upper": "UNITS:"},
                {"x0": 258.0, "x1": 274.0, "y0": 130.0, "y1": 138.0, "text": "BD", "upper": "BD"},
                {"x0": 278.0, "x1": 294.0, "y0": 130.0, "y1": 138.0, "text": "FT", "upper": "FT"},
                {"x0": 300.0, "x1": 304.0, "y0": 130.0, "y1": 138.0, "text": "|", "upper": "|"},
            ],
        )
    ]

    markers = indexer._extract_section_markers(indexer.DOC_TYPE_FACE_FRAME, rows_by_y)
    assert len(markers) == 1
    assert markers[0]["material"] == "3/4 Maple"
    assert markers[0]["unitType"] == "BD_FT"
    assert markers[0]["unitRaw"] == "BD FT"


def test_rows_and_totals_emit_unit_type_with_material_sections(tmp_path, monkeypatch):
    job_dir = tmp_path / "310 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "310 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [
        _w(74, 130, "Material:"),
        _w(124, 130, "'3/4"),
        _w(160, 130, "Paint"),
        _w(194, 130, "Grade"),
        _w(226, 130, "Wood'"),
        _w(255, 130, "|"),
        _w(264, 130, "Units:"),
        _w(305, 130, "BD"),
        _w(322, 130, "FT"),
        _w(338, 130, "|"),
    ]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Part A", "3.0", "24.0", "10 (1)")
    page_words += [_w(75, 210, "Totals"), _w(250, 210, "Width"), _w(320, 210, "Length"), _w(390, 210, "Rips")]
    page_words += [_w(250, 230, "3.0"), _w(320, 230, "24.0"), _w(390, 230, "1")]

    page_words += [
        _w(74, 260, "Material:"),
        _w(124, 260, "'White"),
        _w(170, 260, "Melamine'"),
        _w(232, 260, "|"),
        _w(242, 260, "Units:"),
        _w(280, 260, "SHE"),
        _w(304, 260, "|"),
    ]
    page_words += _std_header(290)
    page_words += _std_row(314, 2, "Part B", "2.0", "30.0", "20 (2)")
    page_words += [_w(75, 340, "Totals"), _w(250, 340, "Width"), _w(320, 340, "Length"), _w(390, 340, "Rips")]
    page_words += [_w(250, 360, "2.0"), _w(320, 360, "30.0"), _w(390, 360, "2")]

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    face = docs[indexer.DOC_TYPE_FACE_FRAME]

    assert [r["unitType"] for r in face["rows"]] == ["BD_FT", "SHEETS"]
    assert [t["unitType"] for t in face["totals"]] == ["BD_FT", "SHEETS"]
    assert face["rows"][0]["material"] == "3/4 Paint Grade Wood"
    assert face["rows"][1]["material"] == "White Melamine"


def test_unit_type_carries_across_spillover_pages(tmp_path, monkeypatch):
    job_dir = tmp_path / "311 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "311 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page1_words = []
    page1_words += [
        _w(74, 130, "Material:"),
        _w(124, 130, "'3/4"),
        _w(160, 130, "Solid"),
        _w(200, 130, "Maple'"),
        _w(236, 130, "|"),
        _w(246, 130, "Units:"),
        _w(284, 130, "BD"),
        _w(301, 130, "FT"),
        _w(317, 130, "|"),
    ]
    page1_words += _std_header(160)
    page1_words += _std_row(184, 1, "Part A", "2.5", "10", "1")

    page2_words = []
    page2_words += _std_row(92, 1, "Part B", "2.5", "12", "2")
    page2_words += _std_row(112, 1, "Part C", "2.5", "14", "3")

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page1_words), _FakePage(words=page2_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert len(rows) == 3
    assert all(r["unitType"] == "BD_FT" for r in rows)


def test_missing_or_unknown_units_mark_unknown_and_warn(tmp_path, monkeypatch):
    job_dir = tmp_path / "312 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "312 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Part A", "2.5", "10", "1")
    page_words += [_w(75, 210, "Totals"), _w(250, 210, "Width"), _w(320, 210, "Length"), _w(390, 210, "Rips")]
    page_words += [_w(250, 230, "2.5"), _w(320, 230, "10"), _w(390, 230, "1")]

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    warnings = []

    def _capture_warning(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        warnings.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "warning", _capture_warning)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    face = docs[indexer.DOC_TYPE_FACE_FRAME]

    assert face["rows"][0]["unitType"] == "UNKNOWN"
    assert face["totals"][0]["unitType"] == "UNKNOWN"
    assert any("HARDWOODS_UNIT_UNKNOWN" in line for line in warnings)


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


def test_mixed_layout_page_parses_pre_header_spillover_before_repeated_header(tmp_path, monkeypatch):
    job_dir = tmp_path / "568 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "568 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    # Page 1: establish geometry + active material.
    page1_words = []
    page1_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Solid"), _w(202, 130, "White"), _w(244, 130, "Oak'")]
    page1_words += _std_header(160)
    page1_words += _std_row(184, 1, "Bottom Rail", "2.5", "20", "10")

    # Page 2: spillover rows before repeated header (real-world failure shape).
    page2_words = []
    page2_words += _std_row(92, 1, "Left Stile", "0.75", "31.75", "20")
    page2_words += _std_row(112, 1, "Right Stile", "0.75", "31.75", "19")
    page2_words += _std_row(132, 1, "Left Stile", "0.75", "17.75", "26")
    page2_words += _std_row(152, 1, "Right Stile", "0.75", "17.75", "26")
    page2_words += [_w(75, 206, "Totals"), _w(250, 206, "Width"), _w(320, 206, "Length"), _w(390, 206, "Rips")]

    # Later repeated section on the same page.
    page2_words += [
        _w(74, 376, "Material:"),
        _w(124, 376, "'3/4"),
        _w(160, 376, "Wilsonart"),
        _w(220, 376, "Coronado"),
        _w(290, 376, "Oak"),
        _w(320, 376, "8244'"),
    ]
    page2_words += _std_header(409)
    page2_words += _std_row(429, 6, "Filler", "1.5", "30.5", "28, 31, 32, 34, 36, 38")

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page1_words), _FakePage(words=page2_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert len(rows) == 6
    page2_rows = [r for r in rows if r["page"] == 2]
    assert len(page2_rows) == 5

    # First four are spillover rows that must not be dropped.
    assert page2_rows[0]["description"] == "Left Stile"
    assert page2_rows[0]["length"] == "31.75"
    assert page2_rows[0]["material"] == "3/4 Solid White Oak"

    assert page2_rows[1]["description"] == "Right Stile"
    assert page2_rows[1]["length"] == "31.75"
    assert page2_rows[1]["material"] == "3/4 Solid White Oak"

    assert page2_rows[2]["description"] == "Left Stile"
    assert page2_rows[2]["length"] == "17.75"
    assert page2_rows[2]["material"] == "3/4 Solid White Oak"

    assert page2_rows[3]["description"] == "Right Stile"
    assert page2_rows[3]["length"] == "17.75"
    assert page2_rows[3]["material"] == "3/4 Solid White Oak"

    # Later section row should still use local material marker.
    assert page2_rows[4]["description"] == "Filler"
    assert page2_rows[4]["material"] == "3/4 Wilsonart Coronado Oak 8244"


def test_logs_warning_when_row_like_lines_exceed_parsed_rows(tmp_path, monkeypatch):
    job_dir = tmp_path / "777 - TEST"
    job_dir.mkdir()
    face_frame = job_dir / "777 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    page_words = []
    # Row-like line before the first detected header (no prior geometry available).
    page_words += _std_row(92, 1, "Left Stile", "0.75", "31.75", "20")
    page_words += [_w(74, 130, "Material:"), _w(124, 130, "'3/4"), _w(160, 130, "Maple'")]
    page_words += _std_header(160)
    page_words += _std_row(184, 1, "Bottom Rail", "4.75", "54", "15, 16")

    doc_map = {str(face_frame): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    warnings = []

    def _capture_warning(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        warnings.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "warning", _capture_warning)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert any("HARDWOODS_ROW_GAP" in line for line in warnings)


def test_legacy_door_list_multi_section_parses_all_rows_without_gap_warning(tmp_path, monkeypatch):
    job_dir = tmp_path / "597 - TEST"
    job_dir.mkdir()
    door_list = job_dir / "597 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 142, "Door"), _w(111, 142, "Type:"), _w(151, 142, "'Coat"), _w(188, 142, "Hook"), _w(220, 142, "Board"), _w(256, 142, "(White"), _w(304, 142, "Oak"), _w(330, 142, "Rift)'")]
    page_words += _legacy_door_header(202.5)
    page_words += [_w(215, 215.7, "|")]
    page_words += _legacy_door_row(241.7, 1, "64.5", "6", "DF", "N", "")
    page_words += [
        _w(74, 273.0, "Door"),
        _w(111, 273.0, "Type:"),
        _w(151, 273.0, "'Plywood"),
        _w(210, 273.0, "Slab"),
        _w(246, 273.0, "Vertical"),
        _w(305, 273.0, "(Rift"),
        _w(338, 273.0, "White"),
        _w(374, 273.0, "Oak"),
        _w(400, 273.0, "PLY)'"),
    ]
    page_words += _legacy_door_header(333.5)
    page_words += [_w(346, 346.7, "|")]
    page_words += _legacy_door_row(372.7, 4, "34.625", "12", "DF", "N", "23 (2), 24 (2)")
    page_words += _legacy_door_row(391.5, 2, "28.875", "12", "DF", "N", "1 (2)")
    page_words += _legacy_door_row(410.3, 4, "24.75", "12", "DF", "N", "30 (4)")
    page_words += _legacy_door_row(429.1, 4, "22.375", "12", "DF", "N", "16 (4)")

    doc_map = {str(door_list): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    warnings = []

    def _capture_warning(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        warnings.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "warning", _capture_warning)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"]

    assert len(rows) == 5
    assert rows[0]["material"] == "Coat Hook Board (White Oak Rift)"
    assert rows[1]["material"] == "Plywood Slab Vertical (Rift White Oak PLY)"
    assert rows[-1]["cabinets"] == ["16"]
    assert not any("HARDWOODS_ROW_GAP" in line for line in warnings)


def test_legacy_door_list_blank_cabinets_parse_without_gap_warning(tmp_path, monkeypatch):
    job_dir = tmp_path / "615 - TEST"
    job_dir.mkdir()
    door_list = job_dir / "615 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    page_words = []
    page_words += [_w(74, 142, "Door"), _w(111, 142, "Type:"), _w(151, 142, "'Slab"), _w(186, 142, "Horizantal"), _w(260, 142, "Grain"), _w(300, 142, "(White"), _w(348, 142, "Oak)'")]
    page_words += _legacy_door_header(202.5)
    page_words += [_w(215, 215.7, "|")]
    page_words += _legacy_door_row(241.7, 4, "14.5", "11.25", "DF", "N", "")
    page_words += _legacy_door_row(260.5, 1, "36", "6", "FF", "N", "")
    page_words += _legacy_door_row(279.3, 2, "14.5", "6", "DF", "N", "")
    page_words += [_w(74, 310.7, "Door"), _w(111, 310.7, "Type:"), _w(151, 310.7, "'Square"), _w(210, 310.7, "Raised"), _w(262, 310.7, "Panel"), _w(304, 310.7, "2"), _w(316, 310.7, '1/4"'), _w(348, 310.7, "(White"), _w(396, 310.7, "Oak)'")]
    page_words += _legacy_door_header(371.2)
    page_words += [_w(384, 384.3, "|")]
    page_words += _legacy_door_row(410.3, 4, "16.3125", "30.5", "P", "P", "4 (4)")
    page_words += _legacy_door_row(429.1, 1, "12.4925", "26.68", "P", "P", "4")

    doc_map = {str(door_list): _FakeDoc([_FakePage(words=page_words)])}
    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])

    warnings = []

    def _capture_warning(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        warnings.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "warning", _capture_warning)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"]

    assert len(rows) == 5
    assert rows[0]["cabinets"] == []
    assert rows[1]["description"] == "Slab Horizantal Grain (White Oak)"
    assert rows[3]["material"] == 'Square Raised Panel 2 1/4" (White Oak)'
    assert not any("HARDWOODS_ROW_GAP" in line for line in warnings)


def test_placeholder_door_list_is_ignored_without_template_mismatch_warning(tmp_path, monkeypatch):
    job_dir = tmp_path / "512 - TEST"
    job_dir.mkdir()
    metadata_dir = job_dir / ".metadata" / "hardwoods"
    metadata_dir.mkdir(parents=True)
    stale = metadata_dir / "cutlist_index.json"
    stale.write_text('{"documents":[]}', encoding="utf-8")
    door_list = job_dir / "512 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    placeholder_words = [
        _w(80, 56.7, "IF"),
        _w(105, 56.7, "YOU'RE"),
        _w(150, 56.7, "SEEING"),
        _w(80, 111.9, "THIS"),
        _w(120, 111.9, "IT"),
        _w(140, 111.9, "MEANS"),
        _w(80, 167.1, "ENGINEER"),
        _w(150, 167.1, "DIDN'T"),
        _w(205, 167.1, "DO"),
    ]
    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([_FakePage(words=placeholder_words)]))

    errors = []

    def _capture_error(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        errors.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "error", _capture_error)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert not stale.exists()
    assert not any("template mismatch" in line.lower() for line in errors)


def test_zero_page_nailer_cut_list_is_skipped_without_template_mismatch_warning(tmp_path, monkeypatch):
    job_dir = tmp_path / "615 - TEST"
    job_dir.mkdir()
    nailer = job_dir / "615 - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(indexer.fitz, "open", lambda path: _FakeDoc([]))

    errors = []

    def _capture_error(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        errors.append(str(rendered))

    monkeypatch.setattr(indexer.main_logger, "error", _capture_error)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is False
    assert not any("template mismatch" in line.lower() for line in errors)


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
