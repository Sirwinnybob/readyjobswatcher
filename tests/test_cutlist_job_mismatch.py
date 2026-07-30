import json
import os
import threading

import ready_jobs_watcher.cutlist_job_mismatch as mismatch


def _override_fields():
    return dict(
        doc_type="NAILER_CUT_LIST",
        pdf_filename="530a - Nailer Cut List.pdf",
        expected_job="530A",
        found_job="532",
    )


def test_override_matches_only_exact_document_identity(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    fields = dict(
        doc_type="NAILER_CUT_LIST",
        pdf_filename="530a - Nailer Cut List.pdf",
        expected_job="530A",
        found_job="532",
    )
    mismatch.allow_job_mismatch_override(str(job_dir), approved_by="operator", **fields)
    assert mismatch.has_job_mismatch_override(str(job_dir), **fields)
    assert not mismatch.has_job_mismatch_override(
        str(job_dir), **{**fields, "found_job": "533"}
    )


def test_removing_final_override_removes_ledger_file(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    fields = dict(
        doc_type="NAILER_CUT_LIST",
        pdf_filename="530a - Nailer Cut List.pdf",
        expected_job="530A",
        found_job="532",
    )
    mismatch.allow_job_mismatch_override(str(job_dir), **fields)
    assert mismatch.remove_job_mismatch_override(str(job_dir), **fields)
    assert not os.path.exists(mismatch.mismatch_override_path(str(job_dir)))


def test_allow_override_leaves_corrupt_ledger_untouched(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    path = mismatch.mismatch_override_path(str(job_dir))
    os.makedirs(os.path.dirname(path))
    original = "{not json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(original)

    assert not mismatch.allow_job_mismatch_override(str(job_dir), **_override_fields())
    with open(path, encoding="utf-8") as f:
        assert f.read() == original


def test_allow_override_leaves_unsupported_ledger_untouched(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    path = mismatch.mismatch_override_path(str(job_dir))
    os.makedirs(os.path.dirname(path))
    original = {"version": 2, "overrides": []}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(original, f)

    assert not mismatch.allow_job_mismatch_override(str(job_dir), **_override_fields())
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == original


def test_allow_override_is_idempotent_and_persists_approval_metadata(tmp_path):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    fields = _override_fields()

    assert mismatch.allow_job_mismatch_override(str(job_dir), approved_by="operator", **fields)
    assert mismatch.allow_job_mismatch_override(str(job_dir), approved_by="different", **fields)

    with open(mismatch.mismatch_override_path(str(job_dir)), encoding="utf-8") as f:
        ledger = json.load(f)
    assert ledger["version"] == 1
    assert ledger["overrides"] == [{
        **fields,
        "approvedAt": ledger["overrides"][0]["approvedAt"],
        "approvedBy": "operator",
    }]
    assert ledger["overrides"][0]["approvedAt"].endswith("+00:00")


def test_concurrent_override_adds_preserve_both_decisions(tmp_path, monkeypatch):
    job_dir = tmp_path / "530a - TEST"
    job_dir.mkdir()
    first_loaded = threading.Event()
    second_loaded = threading.Event()
    real_load = mismatch._load_override_entries

    def coordinated_load(job_folder_path):
        entries = real_load(job_folder_path)
        if threading.current_thread().name == "allow-first":
            first_loaded.set()
            second_loaded.wait(timeout=0.3)
        else:
            second_loaded.set()
        return entries

    monkeypatch.setattr(mismatch, "_load_override_entries", coordinated_load)
    results = []
    first = threading.Thread(
        name="allow-first",
        target=lambda: results.append(mismatch.allow_job_mismatch_override(
            str(job_dir),
            doc_type="NAILER_CUT_LIST",
            pdf_filename="530a - Nailer Cut List.pdf",
            expected_job="530A",
            found_job="532",
        )),
    )
    second = threading.Thread(
        name="allow-second",
        target=lambda: results.append(mismatch.allow_job_mismatch_override(
            str(job_dir),
            doc_type="FACE_FRAME_CUT_LIST",
            pdf_filename="530a - Face Frame Cut List.pdf",
            expected_job="530A",
            found_job="533",
        )),
    )

    first.start()
    assert first_loaded.wait(timeout=1)
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == [True, True]
    with open(mismatch.mismatch_override_path(str(job_dir)), encoding="utf-8") as f:
        ledger = json.load(f)
    identities = {
        (
            entry["doc_type"],
            entry["pdf_filename"],
            entry["expected_job"],
            entry["found_job"],
        )
        for entry in ledger["overrides"]
    }
    assert identities == {
        ("NAILER_CUT_LIST", "530a - Nailer Cut List.pdf", "530A", "532"),
        ("FACE_FRAME_CUT_LIST", "530a - Face Frame Cut List.pdf", "530A", "533"),
    }


def test_parse_job_identifier_plain_number():
    result = mismatch.parse_job_identifier("616")
    assert result == mismatch.JobIdentifier(number="616", suffix=None)


def test_parse_job_identifier_number_with_suffix():
    result = mismatch.parse_job_identifier("616b")
    assert result == mismatch.JobIdentifier(number="616", suffix="B")


def test_parse_job_identifier_rejects_dash_joined_number():
    assert mismatch.parse_job_identifier("123-4") is None


def test_parse_job_identifier_rejects_garbage():
    assert mismatch.parse_job_identifier("DC BIGLEY") is None
    assert mismatch.parse_job_identifier("") is None


def test_extract_pdf_job_identifier_cutlist_style_header():
    lines = [
        "Nailer Cut List 2.0",
        "656 - KENT WITHAM - HICKORY, SHAKER DOORS - 16 July, 2026",
        "Material: '3/4 Prefinished 19mm' | Units:Sheet |",
    ]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines)
    assert result == mismatch.JobIdentifier(number="656", suffix=None)


def test_extract_pdf_job_identifier_suffixed_job_number():
    lines = ["Face Frame Cut List 2.0", "616b - KEVIN JANNI - Default - 17 June, 2026"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_FACE_FRAME, lines)
    assert result == mismatch.JobIdentifier(number="616", suffix="B")


def test_extract_pdf_job_identifier_door_list_style_header():
    lines = ["Door List", "Job: BEECH-NEW BEVEL (582)", "Page 1 of 2"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_DOOR_LIST, lines)
    assert result == mismatch.JobIdentifier(number="582", suffix=None)


def test_extract_pdf_job_identifier_door_list_style_with_suffix():
    lines = ["Door List", "Job: SOME JOB (530a)"]
    result = mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_DOOR_LIST, lines)
    assert result == mismatch.JobIdentifier(number="530", suffix="A")


def test_extract_pdf_job_identifier_no_match_returns_none():
    lines = ["Nailer Cut List 2.0", "Material: '3/4 Maple' | Units:Sheet |"]
    assert mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines) is None


def test_extract_pdf_job_identifier_title_line_not_mistaken_for_job_line():
    # "Nailer Cut List 2.0" must not match the "<num> - ..." pattern.
    lines = ["Nailer Cut List 2.0"]
    assert mismatch.extract_pdf_job_identifier(mismatch.DOC_TYPE_NAILER, lines) is None


def test_is_job_mismatch_same_number_no_suffix_either_side():
    folder_id = mismatch.JobIdentifier(number="656", suffix=None)
    pdf_id = mismatch.JobIdentifier(number="656", suffix=None)
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_folder_has_suffix_pdf_does_not():
    folder_id = mismatch.JobIdentifier(number="616", suffix="B")
    pdf_id = mismatch.JobIdentifier(number="616", suffix=None)
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_pdf_has_suffix_folder_does_not():
    folder_id = mismatch.JobIdentifier(number="616", suffix=None)
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_both_have_same_suffix():
    folder_id = mismatch.JobIdentifier(number="616", suffix="B")
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is False


def test_is_job_mismatch_both_have_different_suffix():
    folder_id = mismatch.JobIdentifier(number="616", suffix="A")
    pdf_id = mismatch.JobIdentifier(number="616", suffix="B")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is True


def test_is_job_mismatch_different_number():
    folder_id = mismatch.JobIdentifier(number="530", suffix="A")
    pdf_id = mismatch.JobIdentifier(number="532", suffix="A")
    assert mismatch.is_job_mismatch(folder_id, pdf_id) is True


def test_folder_job_identifier_extracts_from_folder_name():
    result = mismatch.folder_job_identifier("530a - DC BIGLEY")
    assert result == mismatch.JobIdentifier(number="530", suffix="A")


def test_folder_job_identifier_returns_none_for_unrecognized_name():
    assert mismatch.folder_job_identifier("00 INSTALLATION DRAWINGS (FILLERS, CROWN)") is None
