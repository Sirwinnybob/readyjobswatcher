"""
Entry point for the Ready Jobs Watcher application.
"""
from ready_jobs_watcher.main import setup_logging, Application

if __name__ == "__main__":
    setup_logging()
    app = Application()
    try:
        app.start()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
