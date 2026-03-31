"""
Planka Credential Management Module.

Handles loading and retrieving Planka authentication credentials using secure
system-level storage (e.g., Windows Credential Manager via the `keyring` library),
with a fallback to environment variables.
"""
import os
import logging
import keyring
from .config import Config

planka_logger = logging.getLogger('planka')

# Keyring service name for secure credential storage
KEYRING_SERVICE = "ReadyJobsWatcher"

# Global credentials (set by initialize_planka_credentials)
PLANKABAN_BASE_URL = None
PLANKABAN_USERNAME = None
PLANKABAN_PASSWORD = None

def initialize_planka_credentials(config: Config) -> bool:
    """
    Initialize Planka credentials from config and the system keyring.

    Attempts to load the base URL and username from the provided configuration,
    then securely retrieves the associated password from the system keyring.
    Falls back to environment variables if config settings are absent.

    Args:
        config (Config): Configuration object containing Planka settings.

    Returns:
        bool: True if valid credentials were successfully configured, False otherwise.
    """
    global PLANKABAN_BASE_URL, PLANKABAN_USERNAME, PLANKABAN_PASSWORD

    # First try to load from config
    if config.planka_base_url and config.planka_username:
        PLANKABAN_BASE_URL = config.planka_base_url
        PLANKABAN_USERNAME = config.planka_username

        # Load password from Windows Credential Manager
        try:
            PLANKABAN_PASSWORD = keyring.get_password(KEYRING_SERVICE, config.planka_username)
            if PLANKABAN_PASSWORD:
                planka_logger.info(f"Planka credentials loaded from config and keyring: {PLANKABAN_USERNAME}@{PLANKABAN_BASE_URL}")
                return True
            else:
                planka_logger.warning("Planka password not found in Windows Credential Manager. Planka integration will be disabled.")
                return False
        except Exception as e:
            planka_logger.error(f"Failed to retrieve Planka password from keyring: {e}")
            return False

    # Fallback to environment variables for backwards compatibility
    env_url = os.getenv("PLANKA_BASE_URL")
    env_username = os.getenv("PLANKA_USERNAME")
    env_password = os.getenv("PLANKA_PASSWORD")

    if env_url and env_username and env_password:
        PLANKABAN_BASE_URL = env_url
        PLANKABAN_USERNAME = env_username
        PLANKABAN_PASSWORD = env_password
        planka_logger.info("Planka credentials loaded from environment variables (legacy mode)")
        return True

    # No credentials configured
    planka_logger.warning("Planka credentials not configured. Planka integration will be disabled.")
    planka_logger.warning("Configure credentials in Settings to enable Planka card creation for bad parts.")
    return False

def get_planka_credentials():
    """
    Get the current Planka credentials initialized in the global state.

    Returns:
        tuple: A tuple of (base_url, username, password). If not configured,
               returns (None, None, None).
    """
    return (PLANKABAN_BASE_URL, PLANKABAN_USERNAME, PLANKABAN_PASSWORD)
