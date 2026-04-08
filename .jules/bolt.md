## 2024-05-18 - Prevent ThreadPoolExecutor Exhaustion
Learning: Using `time.sleep()` inside tasks submitted to a `ThreadPoolExecutor` blocks worker threads. If many delayed tasks are scheduled, this can easily exhaust the pool (e.g., 20 workers blocked on 20-minute sleeps), stalling the entire application.
Action: Use `threading.Timer` to offload the wait time to a lightweight daemon thread. The timer's callback should then submit the actual processing work to the `ThreadPoolExecutor`. This keeps the thread pool fully available for actual computational/IO work.
