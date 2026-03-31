"""
Entry point for the Ready Jobs Watcher application.

This module initializes logging and starts the main application loop,
handling graceful shutdown on interruption.
"""
from ready_jobs_watcher.main import setup_logging, Application

if __name__ == "__main__":
    # Initialize the centralized logging system
    setup_logging()

    # Instantiate the core Application
    app = Application()

    try:
        # Start all background threads and system tray interface
        app.start()
    except KeyboardInterrupt:
        # Allow graceful exit via keyboard interrupt
        pass
    finally:
        # Ensure all components and threads are properly terminated
        app.stop()
