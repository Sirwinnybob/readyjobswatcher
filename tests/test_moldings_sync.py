import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import pytest

from ready_jobs_watcher.moldings_sync import sync_moldings_library


class DummyConfig:
    def __init__(self, root_dir):
        self.ROOT_DIR = root_dir
        self.db_server = "DummyServer"
        self.db_name = "DummyDB"
        self.db_user = "DummyUser"
        self.db_password = "DummyPassword"


@pytest.fixture
def mock_config(tmp_path):
    return DummyConfig(str(tmp_path))


@patch("pyodbc.connect")
def test_sync_moldings_library_success(mock_connect, mock_config):
    # Setup mock DB query results
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor.__enter__.return_value = mock_cursor

    # Rows returned: ID, Name, ProfileTypeID, Shape XML
    # Use tuples to mock row access
    mock_cursor.fetchall.return_value = [
        (79, "Crown 1", 1, "<template name='Old Name'><child/></template>"),
        (80, "Scribe 2", 4, "<template><child2/></template>"),
        (81, "Base 3", 5, ""),  # Empty Shape, should be skipped
    ]

    # Pre-populate some legacy and obsolete files in the target moldings directory
    target_dir = os.path.join(mock_config.ROOT_DIR, ".metadata", "moldings")
    os.makedirs(target_dir, exist_ok=True)
    
    # 79.xml, 80.xml and 99.xml are legacy flat files in root target_dir
    legacy_79 = os.path.join(target_dir, "79.xml")
    legacy_80 = os.path.join(target_dir, "80.xml")
    legacy_99 = os.path.join(target_dir, "99.xml")
    for f_path in (legacy_79, legacy_80, legacy_99):
        with open(f_path, "w") as f:
            f.write("<template/>")

    # non_numeric.xml in root should NOT be deleted
    non_numeric_file = os.path.join(target_dir, "non_numeric.xml")
    with open(non_numeric_file, "w") as f:
        f.write("<template/>")

    # Pre-populate obsolete file in Crown subfolder (98.xml is not in db results)
    crown_dir = os.path.join(target_dir, "Crown")
    os.makedirs(crown_dir, exist_ok=True)
    obsolete_sub = os.path.join(crown_dir, "98.xml")
    with open(obsolete_sub, "w") as f:
        f.write("<template/>")

    # Pre-populate non_numeric.xml inside Scribe subfolder (should NOT be deleted)
    scribe_dir = os.path.join(target_dir, "Scribe")
    os.makedirs(scribe_dir, exist_ok=True)
    non_numeric_sub = os.path.join(scribe_dir, "non_numeric.xml")
    with open(non_numeric_sub, "w") as f:
        f.write("<template/>")

    # Run the sync
    success = sync_moldings_library(mock_config)
    assert success is True

    # Verify directory structure and files
    assert os.path.exists(os.path.join(target_dir, "Crown", "79.xml"))
    assert os.path.exists(os.path.join(target_dir, "Scribe", "80.xml"))
    assert not os.path.exists(os.path.join(target_dir, "Base", "81.xml"))  # Skipped because shape is empty
    
    # Check that legacy flat files in root are deleted
    assert not os.path.exists(legacy_79)
    assert not os.path.exists(legacy_80)
    assert not os.path.exists(legacy_99)
    
    # Check that obsolete file inside Crown is deleted
    assert not os.path.exists(obsolete_sub)

    # Check that non-numeric files are kept
    assert os.path.exists(non_numeric_file)
    assert os.path.exists(non_numeric_sub)

    # Verify XML content of modified files
    tree_79 = ET.parse(os.path.join(target_dir, "Crown", "79.xml"))
    root_79 = tree_79.getroot()
    assert root_79.attrib["name"] == "Crown 1"

    tree_80 = ET.parse(os.path.join(target_dir, "Scribe", "80.xml"))
    root_80 = tree_80.getroot()
    assert root_80.attrib["name"] == "Scribe 2"


@patch("pyodbc.connect")
def test_sync_moldings_library_db_connect_error(mock_connect, mock_config):
    # Setup mock to raise connection error
    mock_connect.side_effect = Exception("Connection Failed")

    success = sync_moldings_library(mock_config)
    assert success is False


@patch("pyodbc.connect")
def test_sync_moldings_library_query_error(mock_connect, mock_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor.__enter__.return_value = mock_cursor
    mock_cursor.execute.side_effect = Exception("Query Syntax Error")

    success = sync_moldings_library(mock_config)
    assert success is False


@patch("pyodbc.connect")
def test_sync_moldings_library_xml_parse_error(mock_connect, mock_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor.__enter__.return_value = mock_cursor

    mock_cursor.fetchall.return_value = [
        (79, "Crown 1", 1, "<malformed XML"),  # Malformed, should be skipped
        (80, "Scribe 2", 4, "<template><child2/></template>"),  # Good
    ]

    success = sync_moldings_library(mock_config)
    # The overall operation should succeed (return True), skipping the single malformed XML
    assert success is True

    target_dir = os.path.join(mock_config.ROOT_DIR, ".metadata", "moldings")
    assert not os.path.exists(os.path.join(target_dir, "Crown", "79.xml"))
    assert os.path.exists(os.path.join(target_dir, "Scribe", "80.xml"))


@patch("pyodbc.connect")
def test_sync_moldings_library_skip_identical_writes(mock_connect, mock_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor.__enter__.return_value = mock_cursor

    # Setup database response
    mock_cursor.fetchall.return_value = [
        (79, "Crown 1", 1, "<template><child/></template>"),
    ]

    # Pre-write the exact XML bytes that will be generated to test skip
    target_dir = os.path.join(mock_config.ROOT_DIR, ".metadata", "moldings")
    sub_dir = os.path.join(target_dir, "Crown")
    os.makedirs(sub_dir, exist_ok=True)
    filepath = os.path.join(sub_dir, "79.xml")
    
    # Generate what we expect in the code to ensure identity
    root = ET.fromstring("<template><child/></template>")
    root.set('name', "Crown 1")
    expected_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    
    with open(filepath, "wb") as f:
        f.write(expected_bytes)
        
    initial_mtime = os.path.getmtime(filepath)
    
    # Run the sync
    with patch("ready_jobs_watcher.moldings_sync.atomic_write_bytes") as mock_write:
        success = sync_moldings_library(mock_config)
        assert success is True
        # Since it is identical, atomic_write_bytes should NOT have been called
        mock_write.assert_not_called()
        
    # Check modification time has not changed
    assert os.path.getmtime(filepath) == initial_mtime


@patch("ready_jobs_watcher.main.Application.acquire_lock", return_value=True)
@patch("ready_jobs_watcher.main.Config")
@patch("ready_jobs_watcher.moldings_sync.sync_moldings_library")
def test_application_sync_moldings(mock_sync, mock_config_class, mock_acquire_lock):
    from ready_jobs_watcher.main import Application
    mock_config = MagicMock()
    mock_config_class.return_value = mock_config
    app = Application()
    mock_sync.return_value = True
    
    res = app.sync_moldings()
    assert res is True
    mock_sync.assert_called_once_with(mock_config)

