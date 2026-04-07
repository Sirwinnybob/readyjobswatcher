import pytest
import sys
import ctypes
from unittest.mock import patch, MagicMock
from ready_jobs_watcher.utils import is_hidden, set_hidden_attribute

@pytest.fixture(autouse=True)
def mock_ctypes():
    added = False
    if not hasattr(sys.modules['ctypes'], 'windll'):
        # On non-Windows platforms, create a mock for ctypes.windll.kernel32
        windll_mock = MagicMock()
        setattr(sys.modules['ctypes'], 'windll', windll_mock)
        # Ensure GetLastError is available on the mock or the module
        if not hasattr(sys.modules['ctypes'], 'GetLastError'):
            setattr(sys.modules['ctypes'], 'GetLastError', MagicMock(return_value=0))
        added = True
    yield
    if added:
        delattr(sys.modules['ctypes'], 'windll')

@patch('ctypes.windll.kernel32.GetFileAttributesW', create=True)
def test_is_hidden_true(mock_get_attributes):
    # Mocking the return value of GetFileAttributesW to have the hidden bit (0x2) set
    mock_get_attributes.return_value = 0x2
    assert is_hidden("dummy_folder") is True

@patch('ctypes.windll.kernel32.GetFileAttributesW', create=True)
def test_is_hidden_false(mock_get_attributes):
    # Mocking the return value of GetFileAttributesW to not have the hidden bit (0x2) set
    mock_get_attributes.return_value = 0x0
    assert is_hidden("dummy_folder") is False

@patch('ctypes.windll.kernel32.GetFileAttributesW', create=True)
def test_is_hidden_minus_one(mock_get_attributes):
    # Mocking the return value of GetFileAttributesW to be -1
    mock_get_attributes.return_value = -1
    assert is_hidden("dummy_folder") is False

@patch('ctypes.windll.kernel32.GetFileAttributesW', create=True)
def test_is_hidden_exception(mock_get_attributes):
    # Mocking GetFileAttributesW to raise an exception
    mock_get_attributes.side_effect = Exception("Mocked Exception")
    assert is_hidden("dummy_folder") is False

@patch('ready_jobs_watcher.utils.logging')
@patch('ctypes.windll.kernel32.SetFileAttributesW', create=True)
def test_set_hidden_attribute_success(mock_set_attributes, mock_logging):
    # Mocking success (returns non-zero)
    mock_set_attributes.return_value = 1
    set_hidden_attribute("dummy_path")
    mock_logging.info.assert_called_with("Set hidden attribute on dummy_path")

@patch('ready_jobs_watcher.utils.logging')
@patch('ctypes.windll.kernel32.SetFileAttributesW', create=True)
@patch('ctypes.GetLastError', create=True)
def test_set_hidden_attribute_failure(mock_get_last_error, mock_set_attributes, mock_logging):
    # Mocking failure (returns 0)
    mock_set_attributes.return_value = 0
    mock_get_last_error.return_value = 5  # Access denied
    set_hidden_attribute("dummy_path")
    mock_logging.error.assert_called_with("Failed to set hidden attribute on dummy_path: Error code 5")

@patch('ready_jobs_watcher.utils.logging')
@patch('ctypes.windll.kernel32.SetFileAttributesW', create=True)
def test_set_hidden_attribute_oserror(mock_set_attributes, mock_logging):
    # Mocking OSError
    mock_set_attributes.side_effect = OSError("Access Denied")
    set_hidden_attribute("dummy_path")
    mock_logging.error.assert_called_with("Failed to set hidden attribute on dummy_path: Access Denied")

@patch('ready_jobs_watcher.utils.logging')
@patch('ctypes.windll.kernel32.SetFileAttributesW', create=True)
def test_set_hidden_attribute_exception(mock_set_attributes, mock_logging):
    # Mocking unexpected Exception
    mock_set_attributes.side_effect = Exception("Unexpected")
    set_hidden_attribute("dummy_path")
    mock_logging.error.assert_called_with("Unexpected error setting hidden attribute on dummy_path: Unexpected", exc_info=True)
