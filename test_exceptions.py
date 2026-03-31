import unittest
from unittest.mock import MagicMock, patch
import os
import sys
import subprocess

# Add the current directory to sys.path so we can import the package
sys.path.append(os.getcwd())

# Mock fitz and PIL before importing pdf_dark_mode
sys.modules['fitz'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = MagicMock()
sys.modules['PIL.ImageOps'] = MagicMock()

from ready_jobs_watcher.pdf_dark_mode import run_direct_inversion, run_dark_mode_conversion

class TestPDFDarkModeExceptions(unittest.TestCase):

    @patch('ready_jobs_watcher.pdf_dark_mode.pdf_darkmode_logger')
    @patch('fitz.open')
    def test_run_direct_inversion_oserror(self, mock_fitz_open, mock_logger):
        # Setup
        mock_fitz_open.side_effect = OSError("Simulated OS Error")

        # Execute
        result = run_direct_inversion("in.pdf", "out.pdf")

        # Verify
        self.assertFalse(result)
        mock_logger.error.assert_called_with("Direct inversion failed: Simulated OS Error", exc_info=True)

    @patch('ready_jobs_watcher.pdf_dark_mode.pdf_darkmode_logger')
    @patch('fitz.open')
    def test_run_direct_inversion_unexpected_exception(self, mock_fitz_open, mock_logger):
        # Setup
        mock_fitz_open.side_effect = Exception("Simulated Unexpected Exception")

        # Execute & Verify
        # This should NOT be caught by our new handlers, so it should propagate
        with self.assertRaises(Exception) as cm:
            run_direct_inversion("in.pdf", "out.pdf")
        self.assertEqual(str(cm.exception), "Simulated Unexpected Exception")

    @patch('ready_jobs_watcher.pdf_dark_mode.pdf_darkmode_logger')
    @patch('os.makedirs')
    @patch('subprocess.run')
    @patch('ready_jobs_watcher.pdf_dark_mode.is_dark_mode_available', return_value=True)
    def test_run_dark_mode_conversion_oserror(self, mock_available, mock_run, mock_makedirs, mock_logger):
        # Setup
        mock_run.side_effect = OSError("Subprocess failed")

        # Execute
        result = run_dark_mode_conversion(specific_file="test.pdf")

        # Verify
        self.assertFalse(result)
        mock_logger.error.assert_called_with("Failed to run PDF dark mode conversion: Subprocess failed", exc_info=True)

    @patch('ready_jobs_watcher.pdf_dark_mode.pdf_darkmode_logger')
    @patch('os.makedirs')
    @patch('subprocess.run')
    @patch('ready_jobs_watcher.pdf_dark_mode.is_dark_mode_available', return_value=True)
    def test_run_dark_mode_conversion_unexpected_exception(self, mock_available, mock_run, mock_makedirs, mock_logger):
        # Setup
        mock_run.side_effect = KeyError("Unexpected key error")

        # Execute & Verify
        with self.assertRaises(KeyError):
            run_dark_mode_conversion(specific_file="test.pdf")

if __name__ == '__main__':
    unittest.main()
