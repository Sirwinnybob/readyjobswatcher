@echo off
cd /d "C:\Scripts\Ready Jobs Watcher"
echo Building from: %CD%
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found.
  exit /b 1
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm ready_jobs_watcher.spec
echo Exit code: %ERRORLEVEL%
