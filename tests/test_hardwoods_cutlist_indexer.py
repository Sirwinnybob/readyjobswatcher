import json
import os

import ready_jobs_watcher.hardwoods_cutlist_indexer as indexer


class _FakePage:
    def __init__(self, text: str, *, words=None, text_dict=None):
        self._text = text
        self._words = words or []
        self._text_dict = text_dict or {}

    def get_text(self, mode="text", *_args, **_kwargs):
        if mode == "words":
            return self._words
        if mode == "dict":
            return self._text_dict
        return self._text


class _FakeDoc:
    def __init__(self, page_texts):
        self._pages = [t if isinstance(t, _FakePage) else _FakePage(t) for t in page_texts]
        self.page_count = len(self._pages)

    def __getitem__(self, idx):
        return self._pages[idx]

    def close(self):
        return None


def _load_output(job_dir: str):
    out_path = os.path.join(job_dir, ".metadata", "hardwoods", "cutlist_index.json")
    with open(out_path, "r", encoding="utf-8") as f:
        return out_path, json.load(f)


def test_build_hardwoods_index_writes_expected_schema(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    door_list = job_dir / "998 - Door List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")
    door_list.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(face_frame): _FakeDoc([
            "\n".join(
                [
                    "Face Frame Cut List",
                    "Qty",
                    "Description",
                    "Width",
                    "x",
                    "Length",
                    "Cabinet (Qty)",
                    "2",
                    "Bottom Rail",
                    "4.75 x",
                    "54",
                    "15, 16",
                ]
            )
        ]),
        str(door_list): _FakeDoc([
            "\n".join(
                [
                    "Door List",
                    "Shaker (Paint Grade MDF)",
                    "Qty",
                    "1",
                    "23.875 x 13.5625",
                    "DF",
                    "N",
                    "31 (2), 32 (2)",
                ]
            )
        ]),
    }

    def fake_open(path):
        return doc_map[str(path)]

    monkeypatch.setattr(indexer.fitz, "open", fake_open)

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    out_path, payload = _load_output(str(job_dir))
    assert os.path.exists(out_path)
    assert "generatedAt" in payload
    assert isinstance(payload["documents"], list)

    docs = {doc["docType"]: doc for doc in payload["documents"]}
    assert indexer.DOC_TYPE_FACE_FRAME in docs
    assert indexer.DOC_TYPE_DOOR_LIST in docs

    ff_row = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"][0]
    assert ff_row["qty"] == 2
    assert ff_row["description"] == "Bottom Rail"
    assert ff_row["width"] == "4.75"
    assert ff_row["length"] == "54"
    assert ff_row["cabinets"] == ["15", "16"]
    assert ff_row["rawCabinetText"] == "15, 16"
    assert ff_row["rowId"].startswith(f"{indexer.DOC_TYPE_FACE_FRAME}:1:0:")

    dl_row = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"][0]
    assert dl_row["description"] == "Shaker (Paint Grade MDF)"
    assert dl_row["width"] == "23.875"
    assert dl_row["length"] == "13.5625"
    assert dl_row["cabinets"] == ["31", "32"]

    # Row IDs should be deterministic across rebuilds for identical content.
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload_second = _load_output(str(job_dir))
    docs_second = {doc["docType"]: doc for doc in payload_second["documents"]}
    assert docs_second[indexer.DOC_TYPE_FACE_FRAME]["rows"][0]["rowId"] == ff_row["rowId"]
    assert docs_second[indexer.DOC_TYPE_DOOR_LIST]["rows"][0]["rowId"] == dl_row["rowId"]


def test_build_hardwoods_index_prefers_light_then_dark_fallback(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    dark_dir = job_dir / "DARK MODE"
    dark_dir.mkdir()

    light = job_dir / "998 - Nailer Cut List.pdf"
    dark = dark_dir / "998 - Nailer Cut List.pdf"
    dark.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(dark): _FakeDoc(
            [
                "\n".join(
                    [
                        "Nailer Cut List",
                        "1",
                        "Nailer",
                        "2.25 x",
                        "75.258",
                        "18",
                    ]
                )
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    assert indexer.DOC_TYPE_NAILER in docs
    assert docs[indexer.DOC_TYPE_NAILER]["pdfFilename"] == "998 - Nailer Cut List.pdf"

    # Add light file and ensure it is preferred.
    light.write_text("placeholder", encoding="utf-8")
    doc_map[str(light)] = _FakeDoc(
        [
            "\n".join(
                [
                    "Nailer Cut List",
                    "2",
                    "Nailer",
                    "2.25 x",
                    "58.019",
                    "30 (2)",
                ]
            )
        ]
    )
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload_light = _load_output(str(job_dir))
    docs_light = {doc["docType"]: doc for doc in payload_light["documents"]}
    assert docs_light[indexer.DOC_TYPE_NAILER]["rows"][0]["qty"] == 2
    assert docs_light[indexer.DOC_TYPE_NAILER]["rows"][0]["cabinets"] == ["30"]


def test_build_hardwoods_index_removes_stale_file_when_docs_missing(tmp_path):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    metadata_dir = job_dir / ".metadata" / "hardwoods"
    metadata_dir.mkdir(parents=True)
    stale = metadata_dir / "cutlist_index.json"
    stale.write_text('{"documents":[]}', encoding="utf-8")

    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    assert not stale.exists()


def test_build_hardwoods_index_parses_totals_blocks(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    door_cut = job_dir / "998 - Door Cut List.pdf"
    door_cut.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(door_cut): _FakeDoc(
            [
                "\n".join(
                    [
                        "Door Cut List",
                        "Totals",
                        "Width",
                        "33.19",
                        "30.811",
                        "Length",
                        "24.612",
                        "33.712",
                        "Rips",
                        "2.5",
                        "1/4 MDF",
                        "Qty",
                        "Description",
                    ]
                )
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    assert indexer.DOC_TYPE_DOOR_CUT in docs
    totals = docs[indexer.DOC_TYPE_DOOR_CUT].get("totals", [])
    assert len(totals) == 1
    assert totals[0]["page"] == 1
    assert totals[0]["widthValues"] == ["33.19", "30.811"]
    assert totals[0]["lengthValues"] == ["24.612", "33.712"]
    assert totals[0]["ripsValues"] == ["2.5"]


def test_build_hardwoods_index_merges_wrapped_cabinet_lines(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    door_cut = job_dir / "998 - Door Cut List.pdf"
    door_cut.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(door_cut): _FakeDoc(
            [
                "\n".join(
                    [
                        "Door Cut List",
                        "18",
                        "Door Right Stile",
                        "2.2813 x",
                        "12.0625",
                        "7 (2), 8 (2), 25 (4), 26 (4), 27",
                        "(2), 28 (4)",
                    ]
                )
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_CUT]["rows"]
    assert len(rows) == 1
    assert rows[0]["cabinets"] == ["7", "8", "25", "26", "27", "28"]


def test_build_hardwoods_index_assigns_material_to_totals_with_page_carry(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    nailer = job_dir / "998 - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    page1_words = [
        (10.0, 80.0, 30.0, 88.0, "Totals", 0, 0, 0),
        (50.0, 80.0, 70.0, 88.0, "Width", 0, 0, 1),
        (100.0, 80.0, 130.0, 88.0, "Length", 0, 0, 2),
        (150.0, 80.0, 175.0, 88.0, "Rips", 0, 0, 3),
        (50.0, 95.0, 60.0, 103.0, "2.25", 0, 1, 0),
        (100.0, 95.0, 110.0, 103.0, "75.258", 0, 1, 1),
        (150.0, 95.0, 160.0, 103.0, "1", 0, 1, 2),
        (10.0, 140.0, 30.0, 148.0, "Totals", 1, 0, 0),
        (50.0, 140.0, 70.0, 148.0, "Width", 1, 0, 1),
        (100.0, 140.0, 130.0, 148.0, "Length", 1, 0, 2),
        (150.0, 140.0, 175.0, 148.0, "Rips", 1, 0, 3),
        (50.0, 155.0, 60.0, 163.0, "3", 1, 1, 0),
        (100.0, 155.0, 110.0, 163.0, "99", 1, 1, 1),
    ]
    page1_dict = {
        "blocks": [
            {"lines": [{"bbox": [0.0, 60.0, 200.0, 70.0], "spans": [{"text": "3/4 Prefinished 19mm"}]}]},
            {"lines": [{"bbox": [0.0, 120.0, 200.0, 130.0], "spans": [{"text": "3/4 SOLID MAPLE"}]}]},
        ]
    }
    page1_text = "\n".join(
        [
            "Nailer Cut List",
            "Totals",
            "Width",
            "2.25",
            "Length",
            "75.258",
            "Rips",
            "1",
            "Totals",
            "Width",
            "3",
            "Length",
            "99",
        ]
    )

    page2_words = [
        (10.0, 80.0, 30.0, 88.0, "Totals", 0, 0, 0),
        (50.0, 80.0, 70.0, 88.0, "Width", 0, 0, 1),
        (100.0, 80.0, 130.0, 88.0, "Length", 0, 0, 2),
        (150.0, 80.0, 175.0, 88.0, "Rips", 0, 0, 3),
        (50.0, 95.0, 60.0, 103.0, "4", 0, 1, 0),
        (100.0, 95.0, 110.0, 103.0, "88", 0, 1, 1),
    ]
    page2_dict = {"blocks": []}
    page2_text = "\n".join(["Nailer Cut List", "Totals", "Width", "4", "Length", "88"])

    doc_map = {
        str(nailer): _FakeDoc(
            [
                _FakePage(page1_text, words=page1_words, text_dict=page1_dict),
                _FakePage(page2_text, words=page2_words, text_dict=page2_dict),
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    totals = docs[indexer.DOC_TYPE_NAILER]["totals"]
    assert len(totals) == 3
    assert totals[0]["material"] == "3/4 Prefinished 19mm"
    assert totals[1]["material"] == "3/4 SOLID MAPLE"
    # page 2 has no new material marker; it should carry from prior page.
    assert totals[2]["material"] == "3/4 SOLID MAPLE"


def test_build_hardwoods_index_totals_spillover_continuity_updates_source_pages(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    nailer = job_dir / "998 - Nailer Cut List.pdf"
    nailer.write_text("placeholder", encoding="utf-8")

    page1_words = [
        (10.0, 80.0, 30.0, 88.0, "Totals", 0, 0, 0),
        (50.0, 80.0, 70.0, 88.0, "Width", 0, 0, 1),
        (100.0, 80.0, 130.0, 88.0, "Length", 0, 0, 2),
        (150.0, 80.0, 175.0, 88.0, "Rips", 0, 0, 3),
        (50.0, 95.0, 60.0, 103.0, "2.25", 0, 1, 0),
        (100.0, 95.0, 110.0, 103.0, "75.258", 0, 1, 1),
        (150.0, 95.0, 160.0, 103.0, "1", 0, 1, 2),
    ]
    page2_words = [
        (50.0, 60.0, 60.0, 68.0, "3", 0, 0, 0),
        (100.0, 60.0, 110.0, 68.0, "99", 0, 0, 1),
        (150.0, 60.0, 160.0, 68.0, "2", 0, 0, 2),
        (50.0, 75.0, 60.0, 83.0, "4", 0, 1, 0),
        (100.0, 75.0, 110.0, 83.0, "88", 0, 1, 1),
        (150.0, 75.0, 160.0, 83.0, "3", 0, 1, 2),
    ]
    page3_words = [
        (50.0, 60.0, 60.0, 68.0, "5", 0, 0, 0),
        (100.0, 60.0, 110.0, 68.0, "77", 0, 0, 1),
        (150.0, 60.0, 160.0, 68.0, "4", 0, 0, 2),
    ]

    doc_map = {
        str(nailer): _FakeDoc(
            [
                _FakePage(
                    "\n".join(["Nailer Cut List", "Totals", "Width", "2.25", "Length", "75.258", "Rips", "1"]),
                    words=page1_words,
                    text_dict={"blocks": []},
                ),
                _FakePage("\n".join(["Nailer Cut List", "3", "99", "2"]), words=page2_words, text_dict={"blocks": []}),
                _FakePage("\n".join(["Nailer Cut List", "5", "77", "4"]), words=page3_words, text_dict={"blocks": []}),
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    totals = docs[indexer.DOC_TYPE_NAILER]["totals"]
    assert len(totals) == 1
    assert totals[0]["widthValues"] == ["2.25", "3", "4", "5"]
    assert totals[0]["lengthValues"] == ["75.258", "99", "88", "77"]
    assert totals[0]["ripsValues"] == ["1", "2", "3", "4"]
    assert totals[0]["sourcePages"] == [1, 2, 3]


def test_build_hardwoods_index_rows_include_page_and_row_ordinal_for_stable_sorting(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    face_frame = job_dir / "998 - Face Frame Cut List.pdf"
    face_frame.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(face_frame): _FakeDoc(
            [
                "\n".join(
                    [
                        "Face Frame Cut List",
                        "1",
                        "Top Rail",
                        "2.25 x",
                        "10",
                        "11",
                        "2",
                        "Bottom Rail",
                        "3.5 x",
                        "20",
                        "12, 13",
                    ]
                ),
                "\n".join(
                    [
                        "Face Frame Cut List",
                        "4",
                        "Stile",
                        "1.5 x",
                        "30",
                        "14",
                    ]
                ),
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_FACE_FRAME]["rows"]

    assert [row["page"] for row in rows] == [1, 1, 2]
    assert [row["rowOrdinal"] for row in rows] == [0, 1, 0]
    assert all(isinstance(row["page"], int) for row in rows)
    assert all(isinstance(row["rowOrdinal"], int) for row in rows)
    assert rows == sorted(rows, key=lambda row: (row["page"], row["rowOrdinal"]))


def test_build_hardwoods_index_regeneration_call_remains_true_for_fixture_style(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    door_cut = job_dir / "998 - Door Cut List.pdf"
    door_cut.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(door_cut): _FakeDoc(
            [
                "\n".join(
                    [
                        "Door Cut List",
                        "Totals",
                        "Width",
                        "33.19",
                        "Length",
                        "24.612",
                        "Rips",
                        "2.5",
                    ]
                )
            ]
        )
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_pdf_event(str(door_cut)) is True
    _, first_payload = _load_output(str(job_dir))
    first_totals = {doc["docType"]: doc for doc in first_payload["documents"]}[indexer.DOC_TYPE_DOOR_CUT]["totals"]
    assert first_totals[0]["sourcePages"] == [1]

    # Guard regression for backfill/rebuild style invocations with existing fake fixtures.
    assert indexer.build_hardwoods_cutlist_index_for_pdf_event(str(door_cut)) is True
    _, second_payload = _load_output(str(job_dir))
    second_totals = {doc["docType"]: doc for doc in second_payload["documents"]}[indexer.DOC_TYPE_DOOR_CUT]["totals"]
    assert second_totals[0]["sourcePages"] == [1]


def test_build_hardwoods_index_for_pdf_event_routes_job_folder(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()
    dark_dir = job_dir / "DARK MODE"
    dark_dir.mkdir()

    light_pdf = job_dir / "998 - Door List.pdf"
    dark_pdf = dark_dir / "998 - Door List.pdf"
    other_pdf = job_dir / "998 - ASSEMBLY SHEETS.pdf"
    light_pdf.write_text("placeholder", encoding="utf-8")
    dark_pdf.write_text("placeholder", encoding="utf-8")
    other_pdf.write_text("placeholder", encoding="utf-8")

    called = []

    def fake_build(job_folder):
        called.append(job_folder)
        return True

    monkeypatch.setattr(indexer, "build_hardwoods_cutlist_index_for_job", fake_build)

    assert indexer.build_hardwoods_cutlist_index_for_pdf_event(str(light_pdf)) is True
    assert called[-1] == str(job_dir).replace("/", "\\")

    assert indexer.build_hardwoods_cutlist_index_for_pdf_event(str(dark_pdf)) is True
    assert called[-1] == str(job_dir).replace("/", "\\")

    assert indexer.build_hardwoods_cutlist_index_for_pdf_event(str(other_pdf)) is False


def test_door_list_multiline_section_header_joined(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    door_list = job_dir / "998 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    doc_map = {
        str(door_list): _FakeDoc([
            "\n".join([
                "Door List",
                "Island End Panel No Toe 2 5/8 Stiles (Paint Grade",
                "MDF)",
                "Outside Edge Profile:",
                "Panel Detail:",
                "Inside Edge Profile:",
                "Route Pattern:",
                "Qty",
                "Width x Height",
                "Type",
                "Hinge",
                "Cab (Qty)",
                "2",
                "46.125 x 35.25",
                "BE",
                "N",
                "17, 21",
            ])
        ])
    }

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"]
    assert len(rows) == 1
    assert rows[0]["description"] == "Island End Panel No Toe 2 5/8 Stiles (Paint Grade MDF)"


def test_door_list_description_carries_across_pages(tmp_path, monkeypatch):
    job_dir = tmp_path / "998 - TEST"
    job_dir.mkdir()

    door_list = job_dir / "998 - Door List.pdf"
    door_list.write_text("placeholder", encoding="utf-8")

    page1 = "\n".join([
        "Door List",
        "Slab (White Oak Rift)",
        "Outside Edge Profile:",
        "Panel Detail:",
        "Inside Edge Profile:",
        "Route Pattern:",
        "Qty",
        "Width x Height",
        "Type",
        "Hinge",
        "Cab (Qty)",
        "2",
        "34.375 x 6.375",
        "DF",
        "N",
        "7, 8",
    ])
    page2 = "\n".join([
        "Page 2 of 2",
        "1",
        "38 x 3",
        "S",
        "N",
        "12",
    ])

    doc_map = {str(door_list): _FakeDoc([page1, page2])}

    monkeypatch.setattr(indexer.fitz, "open", lambda path: doc_map[str(path)])
    assert indexer.build_hardwoods_cutlist_index_for_job(str(job_dir)) is True
    _, payload = _load_output(str(job_dir))
    docs = {doc["docType"]: doc for doc in payload["documents"]}
    rows = docs[indexer.DOC_TYPE_DOOR_LIST]["rows"]
    assert len(rows) == 2
    assert rows[0]["description"] == "Slab (White Oak Rift)"
    assert rows[1]["description"] == "Slab (White Oak Rift)"
