import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from ready_jobs_watcher import refresh_signals


class TestRefreshSignalsConcurrency(unittest.TestCase):
    def test_concurrent_touches_to_same_job_all_succeed(self):
        with tempfile.TemporaryDirectory() as job_folder:
            errors = []

            def _touch(i):
                try:
                    refresh_signals.touch_cnc_refresh_signal(
                        job_folder_path=job_folder,
                        reason=f"reason_{i}",
                        source="test",
                    )
                except Exception as exc:  # pragma: no cover - diagnostic only
                    errors.append(exc)

            threads = [threading.Thread(target=_touch, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])

            signal_path = os.path.join(job_folder, "CNC", ".tracker", "watcher_refresh_watcher.json")
            with open(signal_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["source"], "test")

    def test_replace_retries_on_transient_oserror_then_succeeds(self):
        real_replace = os.replace
        call_count = {"n": 0}

        def _flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise PermissionError("[WinError 5] Access is denied")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as job_folder, patch(
            "ready_jobs_watcher.refresh_signals.os.replace", side_effect=_flaky_replace
        ), patch("ready_jobs_watcher.refresh_signals.time.sleep"):
            signal_path = refresh_signals.touch_cnc_refresh_signal(
                job_folder_path=job_folder, reason="r", source="test"
            )

            self.assertEqual(call_count["n"], 3)
            self.assertTrue(os.path.exists(signal_path))

    def test_replace_logs_warning_after_exhausting_retries(self):
        def _always_fails(src, dst):
            raise PermissionError("[WinError 5] Access is denied")

        with tempfile.TemporaryDirectory() as job_folder, patch(
            "ready_jobs_watcher.refresh_signals.os.replace", side_effect=_always_fails
        ), patch("ready_jobs_watcher.refresh_signals.time.sleep"), self.assertLogs(
            level="WARNING"
        ) as log_ctx:
            refresh_signals.touch_cnc_refresh_signal(
                job_folder_path=job_folder, reason="r", source="test"
            )

        self.assertTrue(any("could not write signal file" in m for m in log_ctx.output))


if __name__ == "__main__":
    unittest.main()
