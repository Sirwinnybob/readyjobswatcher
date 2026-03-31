import pytest
from ready_jobs_watcher.config import Config

@pytest.fixture
def config_instance():
    return Config()

def test_validate_config_empty(config_instance):
    """Test validation with an empty config dictionary."""
    assert config_instance._validate_config({}) is True

def test_validate_config_valid_full(config_instance):
    """Test validation with a full, valid config."""
    valid_config = {
        'backup_times': ['00:00', '12:00', '23:59'],
        'pdf_conversion_delay_seconds': 30,
        'new_folder_delay_seconds': 1200.5
    }
    assert config_instance._validate_config(valid_config) is True

def test_validate_config_valid_partial(config_instance):
    """Test validation with a partial valid config."""
    valid_config = {
        'backup_times': ['08:30']
    }
    assert config_instance._validate_config(valid_config) is True

def test_validate_config_invalid_backup_times_type(config_instance):
    """Test validation when backup_times is not a list."""
    invalid_config = {'backup_times': '12:00'}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_invalid_backup_times_item_type(config_instance):
    """Test validation when backup_times contains non-string items."""
    invalid_config = {'backup_times': [1234]}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_invalid_backup_times_format(config_instance):
    """Test validation when backup_times contains strings without a colon."""
    invalid_config = {'backup_times': ['1200']}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_invalid_backup_times_values(config_instance):
    """Test validation when backup_times contains invalid hours or minutes."""
    invalid_configs = [
        {'backup_times': ['24:00']},  # Hour out of range
        {'backup_times': ['12:60']},  # Minute out of range
        {'backup_times': ['-1:00']},  # Negative hour
        {'backup_times': ['12:-1']},  # Negative minute
        {'backup_times': ['ab:cd']},  # Non-numeric
    ]
    for config in invalid_configs:
        assert config_instance._validate_config(config) is False

def test_validate_config_invalid_pdf_conversion_delay_type(config_instance):
    """Test validation when pdf_conversion_delay_seconds is not a number."""
    invalid_config = {'pdf_conversion_delay_seconds': '30'}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_negative_pdf_conversion_delay(config_instance):
    """Test validation when pdf_conversion_delay_seconds is negative."""
    invalid_config = {'pdf_conversion_delay_seconds': -5}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_invalid_new_folder_delay_type(config_instance):
    """Test validation when new_folder_delay_seconds is not a number."""
    invalid_config = {'new_folder_delay_seconds': '1200'}
    assert config_instance._validate_config(invalid_config) is False

def test_validate_config_negative_new_folder_delay(config_instance):
    """Test validation when new_folder_delay_seconds is negative."""
    invalid_config = {'new_folder_delay_seconds': -10.5}
    assert config_instance._validate_config(invalid_config) is False
