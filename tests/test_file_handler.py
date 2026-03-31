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

    # Multiple letters (regex only captures first letter)
    ("123ABC", "123A"),

    # Letters after hyphen (regex ignores them)
    ("123-456A", "123-456"),

    # Leading whitespace (regex anchor ^ requires start of string)
    (" 123-456", None),

    # Symbols and invalid characters
    ("!123", None),
    ("abc-123", None),
    ("123_456", "123"), # _ breaks the \d+-\d+ match, so it matches \d+
    ("123.456", "123"), # . breaks the \d+-\d+ match, so it matches \d+
])
def test_extract_job_number(folder_name, expected):
    assert JobProcessor.extract_job_number(folder_name) == expected

def test_extract_job_number_none_input():
    with pytest.raises(TypeError):
        JobProcessor.extract_job_number(None)
