import ready_jobs_watcher.cutlist_job_mismatch as mismatch


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
