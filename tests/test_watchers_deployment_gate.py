import types
import unittest

from ready_jobs_watcher.watchers import PdfChangeHandler, RenameHandler


class _DummyConfig:
    ROOT_DIR = r"Y:\Ready Jobs"
    pdf_conversion_delay_seconds = 30
    new_folder_delay_seconds = 120


class _DummyJobProcessor:
    def __init__(self):
        self.processed = []
        self.processed_files = []

    def is_job_folder(self, folder_path: str) -> bool:
        return folder_path.lower().startswith("y:\\ready jobs\\")

    def process_job_folder(self, folder_path: str, include_cnc: bool = False):
        self.processed.append((folder_path, include_cnc))

    def extract_job_number(self, folder_name: str):
        return "520r" if folder_name.lower().startswith("520r") else None

    def process_file(self, file_path: str, job_num: str, dir_path: str):
        self.processed_files.append((file_path, job_num, dir_path))


class _DummyAppState:
    def __init__(self):
        self.detected = []

    def on_new_job_folder_detected(self, folder_path: str):
        self.detected.append(folder_path)


class _DummyGate:
    def __init__(self, should_process: bool):
        self._should_process = should_process

    def should_process_job_folder(self, job_folder_path: str) -> bool:
        return self._should_process


class TestWatcherDeploymentGateBehavior(unittest.TestCase):
    def test_top_level_directory_move_enters_pending_flow(self):
        config = _DummyConfig()
        job_processor = _DummyJobProcessor()
        app_state = _DummyAppState()
        handler = RenameHandler(config, job_processor, app_state)

        scheduled = []
        handler._schedule_folder_processing = lambda folder: scheduled.append(folder)  # type: ignore[method-assign]

        event = types.SimpleNamespace(
            src_path=r"Y:\Ready Jobs\Face Frame",
            dest_path=r"Y:\Ready Jobs\520r - BRUCE ALFOR REMAKE",
            is_directory=True,
        )
        handler.on_moved(event)

        self.assertEqual(app_state.detected, [event.dest_path])
        self.assertEqual(scheduled, [event.dest_path])
        self.assertEqual(job_processor.processed, [(event.dest_path, False)])

    def test_dark_mode_conversion_blocked_for_pending_job(self):
        config = _DummyConfig()
        gate = _DummyGate(should_process=False)
        handler = PdfChangeHandler(config, rename_handler=None, deployment_gate=gate)

        should_convert = handler._should_convert_to_dark_mode(
            r"Y:\Ready Jobs\520r - BRUCE ALFOR REMAKE\520r - DELIVERY SHEETS.pdf"
        )
        self.assertFalse(should_convert)

    def test_moved_file_in_job_folder_processed_immediately(self):
        config = _DummyConfig()
        job_processor = _DummyJobProcessor()
        app_state = _DummyAppState()
        handler = RenameHandler(config, job_processor, app_state)

        event = types.SimpleNamespace(
            src_path=r"Y:\Ready Jobs\Frameless\DELIVERY SHEETS.pdf",
            dest_path=r"Y:\Ready Jobs\520r - BRUCE ALFOR REMAKE\DELIVERY SHEETS.pdf",
            is_directory=False,
        )
        handler.on_moved(event)

        self.assertEqual(
            job_processor.processed_files,
            [(event.dest_path, "520r", r"Y:\Ready Jobs\520r - BRUCE ALFOR REMAKE")],
        )

    def test_top_level_directory_rename_flow(self):
        class _DummyAppStateWithRename:
            def __init__(self):
                self.renames = []
                self.detected = []
            def rename_job(self, old_name, new_name):
                self.renames.append((old_name, new_name))
            def on_new_job_folder_detected(self, folder_path):
                self.detected.append(folder_path)

        class _DummyGateWithLoad:
            def __init__(self, deployed):
                self.deployed = deployed
                self.loaded = []
            def load_state(self, job_folder_name):
                self.loaded.append(job_folder_name)
                return {"deployed": self.deployed}

        config = _DummyConfig()
        job_processor = _DummyJobProcessor()
        app_state = _DummyAppStateWithRename()
        gate = _DummyGateWithLoad(deployed=False)
        handler = RenameHandler(config, job_processor, app_state, deployment_gate=gate)

        scheduled = []
        handler._schedule_folder_processing = lambda folder: scheduled.append(folder)  # type: ignore[method-assign]

        event = types.SimpleNamespace(
            src_path=r"Y:\Ready Jobs\123 - OLD JOB",
            dest_path=r"Y:\Ready Jobs\999 - NEW JOB",
            is_directory=True,
        )
        handler.on_moved(event)

        self.assertEqual(app_state.renames, [("123 - OLD JOB", "999 - NEW JOB")])
        self.assertEqual(gate.loaded, ["999 - NEW JOB"])
        self.assertEqual(app_state.detected, [event.dest_path])
        self.assertEqual(scheduled, [event.dest_path])
        self.assertEqual(job_processor.processed, [(event.dest_path, False)])

        app_state_2 = _DummyAppStateWithRename()
        gate_2 = _DummyGateWithLoad(deployed=True)
        handler_2 = RenameHandler(config, job_processor, app_state_2, deployment_gate=gate_2)
        scheduled_2 = []
        handler_2._schedule_folder_processing = lambda folder: scheduled_2.append(folder)  # type: ignore[method-assign]

        handler_2.on_moved(event)
        self.assertEqual(app_state_2.renames, [("123 - OLD JOB", "999 - NEW JOB")])
        self.assertEqual(app_state_2.detected, [])  # Empty because deployed=True


if __name__ == "__main__":
    unittest.main()
