import pytest
import sys
from unittest.mock import patch, MagicMock
from ready_jobs_watcher.utils import is_hidden

@pytest.fixture(autouse=True)
def mock_ctypes():
    added = False
    if not hasattr(sys.modules['ctypes'], 'windll'):
        # On non-Windows platforms, create a mock for ctypes.windll.kernel32
        windll_mock = MagicMock()
        setattr(sys.modules['ctypes'], 'windll', windll_mock)
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
