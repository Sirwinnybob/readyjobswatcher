import pytest
from ready_jobs_watcher.file_handler import JobProcessor

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
    ("123-", "123"),  # Matches \d+ part of \d+[a-zA-Z]?
    ("123-A", "123"), # Matches \d+ part of \d+[a-zA-Z]?
    ("123-456-789", "123-456"),
    ("123AB", "123A"), # Matches \d+[a-zA-Z] part
])
def test_extract_job_number(folder_name, expected):
    assert JobProcessor.extract_job_number(folder_name) == expected

from ready_jobs_watcher.file_handler import should_ignore_file

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

    # Extension matches (IGNORED_EXTENSIONS)
    ("file.tmp", True),
    ("FILE.TMP", True),
    ("backup.bak", True),
    ("data.temp", True),
    ("code.swp", True),

    # Files that should NOT be ignored
    ("document.docx", False),
    ("image.jpg", False),
    ("Thumbs_backup.db", False), # Not exact match or extension
    ("desktop_ini.txt", False),
    ("~not_ignored.txt", False), # Doesn't start with ~$
    ("file.txt", False),
    ("file_without_extension", False),
    (".hidden_file", False), # Not in exact matches or ignored extensions
    ("~$", True), # Exact match to prefix
])
def test_should_ignore_file(filename, expected):
    assert should_ignore_file(filename) == expected
