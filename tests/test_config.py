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


def test_validate_config_bad_parts_mode(config_instance):
    assert config_instance._validate_config({'bad_parts_mode': 'tracker'}) is True
    assert config_instance._validate_config({'bad_parts_mode': 'legacy'}) is True
    assert config_instance._validate_config({'bad_parts_mode': 'unknown'}) is False


def test_validate_config_bad_parts_alert_toggles(config_instance):
    assert config_instance._validate_config({'bad_parts_popup_enabled': True}) is True
    assert config_instance._validate_config({'bad_parts_toast_enabled': False}) is True
    assert config_instance._validate_config({'bad_parts_popup_enabled': 'yes'}) is False
    assert config_instance._validate_config({'bad_parts_toast_enabled': 1}) is False


def test_validate_config_bad_parts_sound_profile(config_instance):
    assert config_instance._validate_config({'bad_parts_sound_profile': 'triple_beep'}) is True
    assert config_instance._validate_config({'bad_parts_sound_profile': 'none'}) is True
    assert config_instance._validate_config({'bad_parts_sound_profile': 'buzz'}) is False


def test_default_new_folder_delay_is_120_seconds(monkeypatch):
    monkeypatch.setattr(Config, "load", lambda self: None)
    cfg = Config()
    assert cfg.new_folder_delay_seconds == 120


def test_metadata_cache_defaults(monkeypatch):
    monkeypatch.setattr(Config, "load", lambda self: None)
    cfg = Config()
    assert cfg.metadata_cache_debounce_seconds == 600
    assert cfg.metadata_end_of_day_time == "20:00"
    assert cfg.metadata_snapshot_enabled is True
    assert cfg.metadata_snapshot_archive_dir.endswith("metadata_snapshots")
    assert cfg.metadata_snapshot_retention_days == 30
    assert cfg.metadata_snapshot_max_per_job == 3
    assert cfg.metadata_snapshot_daypart_limit is True


def test_validate_metadata_cache_config(config_instance):
    assert config_instance._validate_config({"metadata_cache_debounce_seconds": 8}) is True
    assert config_instance._validate_config({"metadata_cache_debounce_seconds": 0}) is True
    assert config_instance._validate_config({"metadata_cache_debounce_seconds": -1}) is False
    assert config_instance._validate_config({"metadata_end_of_day_time": "20:00"}) is True
    assert config_instance._validate_config({"metadata_end_of_day_time": "25:00"}) is False
    assert config_instance._validate_config({"metadata_snapshot_enabled": True}) is True
    assert config_instance._validate_config({"metadata_snapshot_enabled": "yes"}) is False
    assert config_instance._validate_config({"metadata_snapshot_retention_days": 30}) is True
    assert config_instance._validate_config({"metadata_snapshot_retention_days": 0}) is True
    assert config_instance._validate_config({"metadata_snapshot_retention_days": -1}) is False
    assert config_instance._validate_config({"metadata_snapshot_max_per_job": 3}) is True
    assert config_instance._validate_config({"metadata_snapshot_max_per_job": 1}) is True
    assert config_instance._validate_config({"metadata_snapshot_max_per_job": 0}) is False
    assert config_instance._validate_config({"metadata_snapshot_daypart_limit": True}) is True
    assert config_instance._validate_config({"metadata_snapshot_daypart_limit": "yes"}) is False
