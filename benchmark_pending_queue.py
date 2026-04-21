import time
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
import os

from ready_jobs_watcher.pending_queue import PendingQueue

def benchmark():
    print("Setting up benchmark...")

    # We will measure how long it takes to process 10 delayed tasks
    # when the thread pool only has 2 workers.
    # Delay is 1 second.
    # If blocked by time.sleep(), it will take (10/2) * 1s = 5 seconds.
    # If non-blocking (threading.Timer), it will take ~1 second total.

    executor = ThreadPoolExecutor(max_workers=2)
    queue = PendingQueue(queue_file="test_queue.json", executor=executor)

    # Mock handlers
    pdf_handler = MagicMock()
    pdf_handler._cooldown_lock = threading.Lock()
    pdf_handler._conversion_cooldown = {}

    rename_handler = MagicMock()
    rename_handler._pending_folders_lock = threading.Lock()
    rename_handler._pending_folders = {}
    rename_handler.job_processor = MagicMock()

    current_time = time.time()

    # Add 10 pending PDFs, scheduled 1 second from now
    for i in range(10):
        queue.add_pending_pdf(f"dummy_pdf_{i}.pdf", current_time + 1.0)

    start_time = time.time()

    # Mock the actual conversion to just track execution times
    execution_times = []

    def mock_convert(specific_file, invert_images):
        # We need a small block to simulate work, but the main block was the sleep
        time.sleep(0.01)
        execution_times.append(time.time() - start_time)

    with patch('ready_jobs_watcher.pending_queue.os.path.exists', return_value=True):
        # Need to patch the import inside the method
        import sys

        # Create a mock module
        mock_pdf_dark_mode = MagicMock()
        mock_pdf_dark_mode.run_dark_mode_conversion = mock_convert

        # Add it to sys.modules
        sys.modules['ready_jobs_watcher.pdf_dark_mode'] = mock_pdf_dark_mode

        queue.resume_pending_operations(pdf_handler, rename_handler)

        # Wait for all executions to finish (should be ~1.2s max if optimized)
        # Give it up to 6 seconds just in case it's unoptimized
        for _ in range(60):
            if len(execution_times) >= 10:
                break
            time.sleep(0.1)

    total_time = time.time() - start_time

    print(f"Processed 10 items with 2 workers.")
    print(f"Total time taken: {total_time:.2f} seconds.")
    print(f"Expected time if optimized: ~1.0-1.5 seconds.")
    print(f"Expected time if unoptimized (blocking sleep): ~5.0 seconds.")

    if os.path.exists("test_queue.json"):
        os.remove("test_queue.json")
    if os.path.exists("test_queue.json.backup"):
        os.remove("test_queue.json.backup")

if __name__ == "__main__":
    benchmark()
