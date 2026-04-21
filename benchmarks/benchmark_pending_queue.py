import time
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

# Import the current optimized implementation
from ready_jobs_watcher.pending_queue import PendingQueue

def benchmark_pending_queue(use_blocking=False):
    # Setup
    with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tf:
        temp_file = tf.name
        tf.write(b"{}")

    queue = PendingQueue(queue_file=temp_file)
    # Use a small thread pool to easily show starvation
    queue.executor = ThreadPoolExecutor(max_workers=2)

    # Add 5 pending folders, each scheduled to be processed in 2 seconds
    future_time = time.time() + 2.0
    for i in range(5):
        queue.add_pending_folder(f"/fake/path/{i}", future_time)

    pdf_handler = MagicMock()
    pdf_handler._cooldown_lock = threading.Lock()
    pdf_handler._conversion_cooldown = {}

    rename_handler = MagicMock()
    rename_handler._pending_folders_lock = threading.Lock()
    rename_handler._pending_folders = {}
    rename_handler.job_processor = MagicMock()

    if use_blocking:
        original_resume = queue.resume_pending_operations
        def blocking_resume(pdf_h, rename_h):
            current_time = time.time()
            folders_to_resume = queue.get_all_pending_folders()
            for folder_path, info in folders_to_resume.items():
                scheduled_time = info['scheduled_time']
                time_remaining = scheduled_time - current_time
                if time_remaining < 0:
                    time_remaining = 0

                def _delayed_process(path=folder_path, delay=time_remaining):
                    try:
                        time.sleep(delay)
                        queue.remove_pending_folder(path)
                    except Exception:
                        pass

                if queue.executor:
                    queue.executor.submit(_delayed_process)
                else:
                    thread = threading.Thread(target=_delayed_process)
                    thread.start()
        queue.resume_pending_operations = blocking_resume

    start_time = time.time()
    queue.resume_pending_operations(pdf_handler, rename_handler)
    resume_call_time = time.time() - start_time

    # Submit a quick task to the executor to see if it's blocked
    # Since max_workers is 2, and we submitted 5 tasks that wait for 2 seconds,
    # the executor should be blocked for at least 2 seconds if we used time.sleep()
    executor_start_time = time.time()
    future = queue.executor.submit(lambda: 42)
    try:
        result = future.result(timeout=5)
    except Exception as e:
        print(f"Error getting result: {e}")
    executor_delay = time.time() - executor_start_time

    queue.executor.shutdown(wait=False, cancel_futures=True)
    os.remove(temp_file)

    return resume_call_time, executor_delay

print("--- Benchmark Results ---")
baseline_resume, baseline_exec_delay = benchmark_pending_queue(use_blocking=True)
print(f"Baseline (time.sleep):")
print(f"  resume_pending_operations call time: {baseline_resume:.4f}s")
print(f"  Executor availability delay:         {baseline_exec_delay:.4f}s")

optimized_resume, optimized_exec_delay = benchmark_pending_queue(use_blocking=False)
print(f"\nOptimized (threading.Timer):")
print(f"  resume_pending_operations call time: {optimized_resume:.4f}s")
print(f"  Executor availability delay:         {optimized_exec_delay:.4f}s")

print(f"\nImprovement in executor availability: {(baseline_exec_delay / optimized_exec_delay):.2f}x faster")
