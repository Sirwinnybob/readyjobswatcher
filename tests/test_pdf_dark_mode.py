import pytest
import os
import subprocess
from unittest.mock import patch, MagicMock

from ready_jobs_watcher.pdf_dark_mode import (
    is_dark_mode_available,
    run_dark_mode_conversion,
    run_dark_mode_conversion_async,
    PDF_DARK_MODE_CLI_PATH
)

@pytest.fixture
def mock_is_available():
    with patch('ready_jobs_watcher.pdf_dark_mode.is_dark_mode_available', return_value=True) as mock:
        yield mock

@pytest.fixture
def mock_subprocess_run():
    with patch('subprocess.run') as mock:
        # Default mock return value for a successful process
        mock.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
        yield mock

@patch('os.path.exists')
def test_is_dark_mode_available_true(mock_exists):
    mock_exists.return_value = True
    assert is_dark_mode_available() is True
    mock_exists.assert_called_once_with(PDF_DARK_MODE_CLI_PATH)

@patch('os.path.exists')
def test_is_dark_mode_available_false(mock_exists):
    mock_exists.return_value = False
    assert is_dark_mode_available() is False
    mock_exists.assert_called_once_with(PDF_DARK_MODE_CLI_PATH)

def test_run_dark_mode_conversion_not_available():
    with patch('ready_jobs_watcher.pdf_dark_mode.is_dark_mode_available', return_value=False):
        assert run_dark_mode_conversion() is False

def test_run_dark_mode_conversion_skip_dark_mode_folder(mock_is_available):
    # Test path that contains 'DARK MODE' folder
    assert run_dark_mode_conversion(specific_file=r"Y:\Ready Jobs\Project\DARK MODE\file.pdf") is True

@patch('os.makedirs')
def test_run_dark_mode_conversion_specific_file_success(mock_makedirs, mock_is_available, mock_subprocess_run):
    specific_file = r"C:\test\file.pdf"
    result = run_dark_mode_conversion(specific_file=specific_file, theme="claude", dry_run=True, force=True)

    assert result is True
    mock_subprocess_run.assert_called_once()

    # Check the command arguments
    cmd = mock_subprocess_run.call_args[0][0]
    assert "python" in cmd
    assert PDF_DARK_MODE_CLI_PATH in cmd
    assert "--theme" in cmd
    assert "claude" in cmd
    assert "--output" in cmd
    assert "--dry-run" in cmd
    assert "--force" in cmd
    assert specific_file in cmd

@patch('os.makedirs')
def test_run_dark_mode_conversion_folder_scan_success(mock_makedirs, mock_is_available, mock_subprocess_run):
    result = run_dark_mode_conversion()

    assert result is True
    mock_subprocess_run.assert_called_once()

    # Check the command arguments
    cmd = mock_subprocess_run.call_args[0][0]
    assert "--quick-scan" in cmd
    assert "--output" not in cmd

@patch('os.makedirs')
def test_run_dark_mode_conversion_subprocess_failure(mock_makedirs, mock_is_available, mock_subprocess_run):
    # Mock a failed subprocess
    mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

    result = run_dark_mode_conversion()
    assert result is False

@patch('os.makedirs')
def test_run_dark_mode_conversion_timeout(mock_makedirs, mock_is_available, mock_subprocess_run):
    # Mock a timeout exception
    mock_subprocess_run.side_effect = subprocess.TimeoutExpired(cmd="cmd", timeout=600)

    result = run_dark_mode_conversion()
    assert result is False

@patch('os.makedirs')
def test_run_dark_mode_conversion_oserror(mock_makedirs, mock_is_available, mock_subprocess_run):
    # Mock an OSError
    mock_subprocess_run.side_effect = OSError("OS Error")

    result = run_dark_mode_conversion()
    assert result is False

@patch('threading.Thread')
def test_run_dark_mode_conversion_async(mock_thread):
    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance

    run_dark_mode_conversion_async(dry_run=True, theme="sepia", specific_file="test.pdf", force=True)

    mock_thread.assert_called_once()
    mock_thread_instance.start.assert_called_once()

    # Check that kwargs passed correctly (by inspecting the target wrapper locally)
    # The actual inner function arguments are harder to test without calling it,
    # but asserting that thread is created and started validates the async wrapper.
