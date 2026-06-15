import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ready_jobs_watcher.main import Application
from ready_jobs_watcher.deployment_gate import DEPLOYMENT_GATE_FILENAME

class TestReparseJob(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("ready_jobs_watcher.main.clear_old_logs")
    @patch("ready_jobs_watcher.main.build_reference_index_for_job")
    @patch("ready_jobs_watcher.main.build_hardwoods_cutlist_index_for_job")
    @patch("ready_jobs_watcher.main.convert_3d_models_for_job")
    @patch("ready_jobs_watcher.pdf_dark_mode.process_directory")
    def test_reparse_job_cleans_and_calls_parsers(
        self,
        mock_process_directory,
        mock_convert_3d,
        mock_build_hardwoods,
        mock_build_reference,
        mock_clear_logs
    ):
        # Setup mock return values
        mock_build_reference.return_value = True
        mock_build_hardwoods.return_value = True

        # Instantiate Application
        # Mock config's ROOT_DIR to be our temp directory
        with patch("ready_jobs_watcher.main.Config") as mock_config_class:
            mock_config = MagicMock()
            mock_config.ROOT_DIR = self.root
            mock_config.CNC_SUBDIR = "CNC"
            mock_config_class.return_value = mock_config

            app = Application()

            # Create mock job folder structure
            job_name = "123-TEST_JOB"
            job_path = os.path.join(self.root, job_name)
            os.makedirs(job_path, exist_ok=True)

            # Create directories to be deleted
            dark_mode_dir = os.path.join(job_path, "DARK MODE")
            os.makedirs(dark_mode_dir, exist_ok=True)
            with open(os.path.join(dark_mode_dir, "some_inverted.pdf"), "w") as f:
                f.write("pdf data")

            cnc_metadata_dir = os.path.join(job_path, "CNC", ".metadata")
            os.makedirs(cnc_metadata_dir, exist_ok=True)
            candidates_file_path = os.path.join(cnc_metadata_dir, "remake_bad_parts_candidates.json")
            with open(candidates_file_path, "w") as f:
                f.write("candidates")
            # Create a file that should NOT be deleted (external metadata)
            external_cnc_metadata_file = os.path.join(cnc_metadata_dir, "sheet_1.json")
            with open(external_cnc_metadata_file, "w") as f:
                f.write("external sheet metadata")

            three_d_dir = os.path.join(job_path, "3D", "Room_1")
            os.makedirs(three_d_dir, exist_ok=True)
            glb_file_path = os.path.join(three_d_dir, "3d_medium.glb")
            with open(glb_file_path, "w") as f:
                f.write("glb data")

            metadata_dir = os.path.join(job_path, ".metadata")
            os.makedirs(metadata_dir, exist_ok=True)
            with open(os.path.join(metadata_dir, DEPLOYMENT_GATE_FILENAME), "w") as f:
                f.write('{"deployed": true, "parseReady": true}')
            with open(os.path.join(metadata_dir, "cabinet_sheet_index.json"), "w") as f:
                f.write("index data")
            with open(os.path.join(metadata_dir, "cache_static.json"), "w") as f:
                f.write("cache data")

            # Setup App settings window mock
            app.settings_window = MagicMock()

            # Run reparse_job
            app.job_processor = MagicMock()
            result = app.reparse_job(job_name)

            # Assertions
            self.assertTrue(result)

            # Check that files/dirs were deleted
            self.assertFalse(os.path.exists(dark_mode_dir))
            self.assertFalse(os.path.exists(candidates_file_path))
            self.assertTrue(os.path.exists(external_cnc_metadata_file)) # Ensure external file is preserved
            self.assertFalse(os.path.exists(glb_file_path))
            self.assertFalse(os.path.exists(os.path.join(metadata_dir, "cabinet_sheet_index.json")))
            self.assertFalse(os.path.exists(os.path.join(metadata_dir, "cache_static.json")))

            # Check that deployment_gate.json was preserved
            self.assertTrue(os.path.exists(os.path.join(metadata_dir, DEPLOYMENT_GATE_FILENAME)))

            # Check that mock parsers were called
            app.job_processor.process_job_folder.assert_called_once_with(job_path)
            mock_build_reference.assert_called_once_with(job_path)
            mock_build_hardwoods.assert_called_once_with(job_path, deployment_gate=app.deployment_gate)
            mock_convert_3d.assert_called_once_with(job_path)
            mock_process_directory.assert_called_once_with(job_path, force=True)

            # Check deployment gate state updates (parseReady should end up True since build functions returned True)
            state = app.deployment_gate.load_state(job_name)
            self.assertTrue(state["parseReady"])

            # Check settings GUI was refreshed
            app.settings_window.refresh_jobs_dashboard.assert_called_once()

if __name__ == "__main__":
    unittest.main()

