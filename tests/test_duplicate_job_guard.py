from ready_jobs_watcher.duplicate_job_guard import (
    clear_duplicate_suspect_marker,
    find_job_number_collision,
    read_duplicate_suspect_marker,
    write_duplicate_suspect_marker,
)


def test_find_job_number_collision_detects_shared_number(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "649 - HARTFORD McCASLIN REFACE", "649")

    assert collision is None  # only one folder has "649"


def test_find_job_number_collision_returns_other_folder_name(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "502 - HARTFORD McCASLIN REFACE").mkdir(parents=True)
    (root / "502 - HARTFORD MCCASLIN REFACE COPY").mkdir(parents=True)

    collision = find_job_number_collision(str(root), "502 - HARTFORD MCCASLIN REFACE COPY", "502")

    assert collision == "502 - HARTFORD McCASLIN REFACE"


def test_write_and_read_duplicate_suspect_marker(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)

    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")
    marker = read_duplicate_suspect_marker(str(root), job.name)

    assert marker["suspectedDuplicateOf"] == "502 - HARTFORD McCASLIN REFACE"
    assert marker["reason"] == "job_number_collision"
    assert "detectedAt" in marker


def test_read_duplicate_suspect_marker_returns_none_when_absent(tmp_path):
    root = tmp_path / "Ready Jobs"
    (root / "649 - HARTFORD McCASLIN REFACE").mkdir(parents=True)

    assert read_duplicate_suspect_marker(str(root), "649 - HARTFORD McCASLIN REFACE") is None


def test_clear_duplicate_suspect_marker_removes_file(tmp_path):
    root = tmp_path / "Ready Jobs"
    job = root / "502 - HARTFORD MCCASLIN REFACE COPY"
    job.mkdir(parents=True)
    write_duplicate_suspect_marker(str(root), job.name, "502 - HARTFORD McCASLIN REFACE")

    clear_duplicate_suspect_marker(str(root), job.name)

    assert read_duplicate_suspect_marker(str(root), job.name) is None
    # Clearing an already-absent marker must not raise.
    clear_duplicate_suspect_marker(str(root), job.name)
