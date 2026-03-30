@echo off
REM Start Ready Jobs Watcher silently (no console window)
REM This batch file can be placed in Windows Startup folder

REM Set environment variables for Planka (optional)
REM Uncomment and set these if you want to use Planka integration:
REM set PLANKA_BASE_URL=http://192.168.1.15:30064
REM set PLANKA_USERNAME=your_username
REM set PLANKA_PASSWORD=your_password
REM set PLANKA_TIMEOUT=10

REM Start the application
start "" "C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe"

REM Exit without keeping window open
exit
