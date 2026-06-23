"""
Light launcher for Ready Jobs Watcher.

Runs the app directly from the repo using the local .venv, rather than
bundling all dependencies into the exe. This makes rebuilds near-instant.
"""
import subprocess
import os
import ctypes

REPO_DIR = r"C:\Scripts\Ready Jobs Watcher"
PYTHONW = os.path.join(REPO_DIR, ".venv", "Scripts", "pythonw.exe")

if not os.path.exists(PYTHONW):
    ctypes.windll.user32.MessageBoxW(
        0,
        f"Python not found at:\n{PYTHONW}\n\nEnsure the .venv exists in the repo folder.",
        "ReadyJobsWatcher Light — Error",
        0x10,  # MB_ICONERROR
    )
else:
    subprocess.Popen(
        [PYTHONW, "-m", "ready_jobs_watcher"],
        cwd=REPO_DIR,
        close_fds=True,
    )
