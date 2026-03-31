import pytest
from ready_jobs_watcher.file_handler import JobProcessor, should_ignore_file


# ==================== extract_job_number tests ====================

@pytest.mark.parametrize("folder_name, expected", [
    # Standard job numbers
    ("123-456", "123-456"),
    ("123", "123"),
    ("123A", "123A"),
    ("123a", "123a"),

    # Job numbers with folder descriptions
    ("123-456 - Project Alpha", "123-456"),
    ("123 - Client Beta", "123"),
    ("123A - Special Job", "123A"),

    # Edge cases
    ("", None),
    ("Project 123", None),
    ("A123", None),
    ("-123", None),
    ("123-", "123"),
    ("123-A", "123"),
    ("123-456-789", "123-456"),
    ("123AB", "123A"),
    ("123ABC", "123A"),
    ("123-456A", "123-456"),
    (" 123-456", None),           # leading whitespace
    ("!123", None),
    ("abc-123", None),
    ("123_456", "123"),
    ("123.456", "123"),
])
def test_extract_job_number(folder_name, expected):
    assert JobProcessor.extract_job_number(folder_name) == expected


def test_extract_job_number_none_input():
    """Ensure it raises TypeError when passed None (as expected by the implementation)."""
    with pytest.raises(TypeError):
        JobProcessor.extract_job_number(None)


# ==================== should_ignore_file tests ====================

@pytest.mark.parametrize("filename, expected", [
    # Exact matches (IGNORED_FILES)
    ("thumbs.db", True),
    ("Thumbs.db", True),
    ("THUMBS.DB", True),
    ("desktop.ini", True),
    (".ds_store", True),
    (".DS_Store", True),

    # Prefix matches (Office temp files)
    ("~$document.docx", True),
    ("~$spreadsheet.xlsx", True),
    ("~$temp", True),
    ("~$", True),

    # Extension matches (IGNORED_EXTENSIONS)
    ("file.tmp", True),
    ("FILE.TMP", True),
    ("backup.bak", True),
    ("data.temp", True),
    ("code.swp", True),

    # Files that should NOT be ignored
    ("document.docx", False),
    ("image.jpg", False),
    ("Thumbs_backup.db", False),
    ("desktop_ini.txt", False),
    ("~not_ignored.txt", False),
    ("file.txt", False),
    ("file_without_extension", False),
    (".hidden_file", False),
])
def test_should_ignore_file(filename, expected):
    assert should_ignore_file(filename) == expected