"""
Entry point for the Ready Jobs Watcher application.

This module initializes logging and starts the main application loop,
handling graceful shutdown on interruption.
"""
import os
import sys

# Ensure the project root is in sys.path so we can import 'ready_jobs_watcher' as a package
# This allows the script to be run directly: python ready_jobs_watcher\__main__.py
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from ready_jobs_watcher.main import setup_logging, Application

if __name__ == "__main__":
    # Initialize the centralized logging system
    setup_logging()

    # Instantiate the core Application
    from ready_jobs_watcher import __version__
    print(f"Ready Jobs Watcher v{__version__} starting...")
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
