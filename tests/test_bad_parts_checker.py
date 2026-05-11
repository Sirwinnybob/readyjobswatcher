import pytest
import json
import os
import sys
from unittest.mock import patch, mock_open, MagicMock

# Mock dependencies that might be missing in the environment before importing bad_parts_checker
def mock_missing_deps():
    mock_fitz = MagicMock()
    mock_pil = MagicMock()
    mock_winotify = MagicMock()
    mock_pystray = MagicMock()
    mock_sv_ttk = MagicMock()
    mock_psutil = MagicMock()

    if "fitz" not in sys.modules:
        sys.modules["fitz"] = mock_fitz
    if "PIL" not in sys.modules:
        sys.modules["PIL"] = mock_pil
        sys.modules["PIL.Image"] = mock_pil.Image
    if "winotify" not in sys.modules:
        sys.modules["winotify"] = mock_winotify
    if "pystray" not in sys.modules:
        sys.modules["pystray"] = mock_pystray
    if "sv_ttk" not in sys.modules:
        sys.modules["sv_ttk"] = mock_sv_ttk
    if "psutil" not in sys.modules:
        sys.modules["psutil"] = mock_psutil

mock_missing_deps()

from ready_jobs_watcher import bad_parts_checker

@pytest.fixture
def reset_blacklist():
    """Fixture to reset the BLACKLISTED_FILES global set before each test."""
    bad_parts_checker.BLACKLISTED_FILES = set()
    yield
    bad_parts_checker.BLACKLISTED_FILES = set()

# ==================== save_to_blacklist tests ====================

def test_save_to_blacklist_success(reset_blacklist):
    """Verify that an entry is added to the set and written to the file as JSON."""
    pdf_path = "test.pdf"
    page_num = 0

    m = mock_open()
    with patch("builtins.open", m):
        with patch("json.dump") as mock_json_dump:
            bad_parts_checker.save_to_blacklist(pdf_path, page_num)

            # Check if added to set
            assert (pdf_path, page_num) in bad_parts_checker.BLACKLISTED_FILES

            # Check if file was opened for writing
            m.assert_called_once_with(bad_parts_checker.BLACKLIST_FILE, 'w')

            # Check if json.dump was called with the correct data
            # BLACKLISTED_FILES is a set of tuples, save_to_blacklist converts it to list of lists
            expected_data = [[pdf_path, page_num]]
            mock_json_dump.assert_called_once()
            args, _ = mock_json_dump.call_args
            assert args[0] == expected_data

def test_save_to_blacklist_error(reset_blacklist):
    """Verify that exceptions during file write are caught and logged."""
    pdf_path = "test.pdf"
    page_num = 0

    with patch("builtins.open", side_effect=OSError("Permission denied")):
        with patch.object(bad_parts_checker.badparts_logger, "error") as mock_log_error:
            bad_parts_checker.save_to_blacklist(pdf_path, page_num)

            # Even if saving fails, it should be added to the in-memory set
            assert (pdf_path, page_num) in bad_parts_checker.BLACKLISTED_FILES

            # Verify error was logged
            mock_log_error.assert_called_once()
            assert "Failed to save blacklist file" in mock_log_error.call_args[0][0]

# ==================== load_blacklist tests ====================

def test_load_blacklist_success(reset_blacklist):
    """Verify that data is correctly loaded from a JSON file into the global set."""
    pdf_path = "test.pdf"
    page_num = 0
    json_data = [[pdf_path, page_num]]

    with patch("os.path.exists", return_value=True):
        with patch("os.path.getsize", return_value=100):
            with patch("builtins.open", mock_open(read_data=json.dumps(json_data))):
                bad_parts_checker.load_blacklist()

                assert (pdf_path, page_num) in bad_parts_checker.BLACKLISTED_FILES
                assert len(bad_parts_checker.BLACKLISTED_FILES) == 1

def test_load_blacklist_not_exists(reset_blacklist):
    """Verify that an empty set is initialized if the file doesn't exist."""
    with patch("os.path.exists", return_value=False):
        bad_parts_checker.load_blacklist()
        assert len(bad_parts_checker.BLACKLISTED_FILES) == 0

def test_load_blacklist_empty_file(reset_blacklist):
    """Verify that an empty set is initialized if the file is empty."""
    with patch("os.path.exists", return_value=True):
        with patch("os.path.getsize", return_value=0):
            bad_parts_checker.load_blacklist()
            assert len(bad_parts_checker.BLACKLISTED_FILES) == 0

def test_load_blacklist_malformed(reset_blacklist):
    """Verify that an empty set is initialized if the JSON is malformed."""
    with patch("os.path.exists", return_value=True):
        with patch("os.path.getsize", return_value=100):
            with patch("builtins.open", mock_open(read_data="not json")):
                with patch.object(bad_parts_checker.badparts_logger, "warning") as mock_log_warning:
                    bad_parts_checker.load_blacklist()
                    assert len(bad_parts_checker.BLACKLISTED_FILES) == 0
                    mock_log_warning.assert_called_once()
                    assert "is malformed" in mock_log_warning.call_args[0][0]

def test_load_blacklist_generic_error(reset_blacklist):
    """Verify that an error is logged if an unexpected exception occurs."""
    with patch("os.path.exists", side_effect=Exception("Unexpected error")):
        with patch.object(bad_parts_checker.badparts_logger, "error") as mock_log_error:
            bad_parts_checker.load_blacklist()
            assert len(bad_parts_checker.BLACKLISTED_FILES) == 0
            mock_log_error.assert_called_once()
            assert "error loading blacklist file" in mock_log_error.call_args[0][0].lower()
