@echo off
echo ========================================
echo Building Ready Jobs Watcher Executable
echo ========================================
echo.

REM Clean previous build
echo Cleaning previous build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
echo.

REM Build the executable
echo Building executable with PyInstaller...
pyinstaller ready_jobs_watcher.spec
echo.

if exist "dist\ReadyJobsWatcher\ReadyJobsWatcher.exe" (
    echo ========================================
    echo Build successful!
    echo ========================================
    echo.
    echo Executable location:
    echo %cd%\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
    echo.
    echo You can now run the application from:
    echo dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
    echo.
) else (
    echo ========================================
    echo Build FAILED!
    echo ========================================
    echo Please check the error messages above.
)

pause
