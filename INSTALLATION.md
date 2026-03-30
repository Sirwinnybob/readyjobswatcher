# Ready Jobs Watcher - Installation Guide

## Executable Location

After building with PyInstaller, the executable is located at:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
```

## Quick Start

### Option 1: Manual Start
Simply double-click:
```
C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe
```

Or use the provided batch file:
```
C:\Scripts\Ready Jobs Watcher\Start_ReadyJobsWatcher.bat
```

### Option 2: Windows Startup (Recommended)

To run the application automatically when Windows starts:

#### Method 1: Using Startup Folder (Easiest)

1. Press `Win + R` to open Run dialog
2. Type `shell:startup` and press Enter
3. This opens your Startup folder: `C:\Users\YourUsername\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup`
4. Create a shortcut to the executable in this folder:
   - Right-click in the Startup folder
   - Select "New" ’ "Shortcut"
   - Browse to `C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe`
   - Click "Next" and "Finish"

**OR** copy the batch file:
   - Copy `C:\Scripts\Ready Jobs Watcher\Start_ReadyJobsWatcher.bat`
   - Paste into the Startup folder

#### Method 2: Using Task Scheduler (Advanced)

For more control (e.g., run with elevated privileges):

1. Open Task Scheduler (`Win + R` ’ `taskschgr.msc`)
2. Click "Create Basic Task"
3. Name: `Ready Jobs Watcher`
4. Trigger: "When I log on"
5. Action: "Start a program"
6. Program: `C:\Scripts\Ready Jobs Watcher\dist\ReadyJobsWatcher\ReadyJobsWatcher.exe`
7. Finish

**Advanced Options**:
- Check "Run with highest privileges" if needed
- Set "Start in" directory to: `C:\Scripts\Ready Jobs Watcher`

## Environment Variables (Optional)

For Planka integration, set these environment variables:

### Using System Environment Variables:

1. Press `Win + R` ’ type `sysdm.cpl` ’ press Enter
2. Go to "Advanced" tab ’ "Environment Variables"
3. Under "User variables" or "System variables", click "New"
4. Add the following variables:

```
Variable Name: PLANKA_BASE_URL
Value: http://192.168.1.15:30064

Variable Name: PLANKA_USERNAME
Value: your_planka_username

Variable Name: PLANKA_PASSWORD
Value: your_planka_password

Variable Name: PLANKA_TIMEOUT
Value: 10
```

5. Click OK and restart the application

### Using Batch File:

Edit `Start_ReadyJobsWatcher.bat` and uncomment the SET commands:

```batch
set PLANKA_BASE_URL=http://192.168.1.15:30064
set PLANKA_USERNAME=your_username
set PLANKA_PASSWORD=your_password
set PLANKA_TIMEOUT=10
```

## Application Features

The Ready Jobs Watcher automatically:

1. **File Processing**
   - Monitors `Y:\Ready Jobs` for new/renamed job folders
   - Renames files with job number prefix
   - Retries locked files every 15 minutes

2. **PDF Dark Mode Conversion**
   - Runs automatically before every backup
   - Runs after file changes (with 5-minute cooldown)
   - Uses the PDF Dark Mode Converter at: `C:\Scripts\PDF DARK MODE\pdf-dark-mode-converter\cli.py`
   - Converts all PDFs in `Y:\Ready Jobs` silently in the background

3. **Bad Parts Detection**
   - Scans PDFs for "BAD PART(S)" marks
   - Creates desktop log: `Desktop\Bad Parts Log.txt`
   - Creates Planka cards automatically (if configured)
   - Scheduled scan: Monday-Thursday at 09:35, Friday at 09:05

4. **Automated Backups**
   - Default times: 00:00 and 12:00 daily
   - Backs up `Y:\Ready Jobs` and `Y:\Upcoming Jobs` to `C:\Syncthing Backup`
   - Runs PDF dark mode conversion before each backup
   - Automatically deletes backups older than 7 days

5. **System Tray Icon**
   - Access settings via system tray
   - Manual triggers: Backup Now, Scan CNC Now, Convert PDFs to Dark Mode
   - Quit application from tray

## GUI Settings

Right-click the system tray icon ’ "Open Settings" to:

- View last backup time and next scheduled backup
- Change backup schedule (two daily times)
- Manually trigger backup
- Manually scan Ready Jobs folder
- Manually trigger PDF dark mode conversion
- View count of pending file renames

## Troubleshooting

### Application won't start
- Check that all dependencies are in the `dist\ReadyJobsWatcher` folder
- Check logs in: `C:\Scripts\Ready Jobs Watcher\logs\`

### PDF Dark Mode not working
- Verify CLI exists at: `C:\Scripts\PDF DARK MODE\pdf-dark-mode-converter\cli.py`
- Check the PDF dark mode config.json is properly configured
- Check application logs for errors

### Planka integration not working
- Verify environment variables are set correctly
- Check Planka server is accessible
- Check credentials are correct
- Check logs for connection errors

### Files not being renamed
- Check `Y:\Ready Jobs` is accessible
- Ensure job folders match pattern: `123-456` or `123A`
- Check application logs

### Backup not running
- Verify `C:\Syncthing Backup` folder exists and is writable
- Check backup schedule in settings
- View logs for errors

## Logs

Application logs are stored in:
```
C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.log
```

Separate logs for:
- `main` - Main application
- `backup` - Backup operations
- `badparts` - Bad parts detection
- `planka` - Planka integration
- `pdf_darkmode` - PDF dark mode conversion
- `cnc` - CNC scanning

## Rebuilding the Executable

If you make code changes and need to rebuild:

1. Run the build script:
   ```
   cd "C:\Scripts\Ready Jobs Watcher"
   build.bat
   ```

2. Or manually:
   ```
   pyinstaller ready_jobs_watcher.spec
   ```

The new executable will be in `dist\ReadyJobsWatcher\ReadyJobsWatcher.exe`

## Uninstallation

1. Remove from Startup:
   - Delete shortcut from `shell:startup` folder
   - Or disable in Task Scheduler

2. Close the application:
   - Right-click system tray icon ’ Quit

3. Delete the folder:
   ```
   C:\Scripts\Ready Jobs Watcher
   ```

4. Remove environment variables (if set)

## Support

For issues or questions:
- Check logs in `C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.log`
- Review this documentation
- Check error messages in log files
