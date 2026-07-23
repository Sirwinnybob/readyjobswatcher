<#
.SYNOPSIS
    Read-only audit of the Ready Jobs Watcher launcher/deployment policy.

.DESCRIPTION
    Reports (does not change) the current state needed to confirm exactly
    one supported watcher launcher is configured and exactly one watcher
    process is running:

      - The supported scheduled task, 'ReadyJobsWatcher' (actions/triggers/
        state/settings, including MultipleInstances policy).
      - The legacy scheduled task, 'Ready Jobs Watcher' (with spaces), which
        must remain disabled.
      - Any currently running Ready Jobs Watcher processes (PID, PPID,
        executable path, command line), sourced from Win32_Process so
        duplicates - however they were started - are visible.
      - The diagnostic PID file content, if present.

    This script is READ-ONLY BY DESIGN. It must never contain Stop-Process,
    Disable-ScheduledTask, Unregister-ScheduledTask, Remove-Item, or any
    other command that changes machine state. Run it before AND after any
    deployment step; it never performs the deployment step itself.

.NOTES
    Run from the project root:
        powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\verify_ready_jobs_launcher.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$SupportedTaskName = 'ReadyJobsWatcher'
$LegacyTaskName = 'Ready Jobs Watcher'
$DiagnosticPidPath = 'C:\Scripts\Ready Jobs Watcher\ready_jobs_watcher.lock'

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host "==== $Title ====" -ForegroundColor Cyan
}

function Show-ScheduledTaskReport {
    param(
        [string]$TaskName,
        [switch]$AllowMissing
    )

    if ($AllowMissing) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } else {
        $task = Get-ScheduledTask -TaskName $TaskName
    }

    if (-not $task) {
        Write-Host "Task '$TaskName' not found." -ForegroundColor Yellow
        return $null
    }

    Write-Host "Task Name : $($task.TaskName)"
    Write-Host "State     : $($task.State)"

    $settings = $task.Settings
    if ($settings) {
        Write-Host "MultipleInstancesPolicy=$($settings.MultipleInstances)"
    } else {
        Write-Host 'MultipleInstancesPolicy=<unavailable>'
    }

    Write-Host 'Actions:'
    foreach ($action in $task.Actions) {
        $execPath = $null
        $execArgs = $null
        try { $execPath = $action.Execute } catch { }
        try { $execArgs = $action.Arguments } catch { }
        Write-Host "  - Execute='$execPath' Arguments='$execArgs'"
    }

    Write-Host 'Triggers:'
    if ($task.Triggers -and $task.Triggers.Count -gt 0) {
        foreach ($trigger in $task.Triggers) {
            Write-Host "  - $($trigger.CimClass.CimClassName): Enabled=$($trigger.Enabled) StartBoundary=$($trigger.StartBoundary)"
        }
    } else {
        Write-Host '  (none)'
    }

    return $task
}

Write-Section "Supported task: $SupportedTaskName"
$supportedTask = Show-ScheduledTaskReport -TaskName $SupportedTaskName

Write-Section "Legacy task: $LegacyTaskName (must remain Disabled)"
$legacyTask = Show-ScheduledTaskReport -TaskName $LegacyTaskName -AllowMissing
if ($legacyTask -and $legacyTask.State -ne 'Disabled') {
    Write-Host "WARNING: legacy task '$LegacyTaskName' is not Disabled (State=$($legacyTask.State))." -ForegroundColor Red
} elseif ($legacyTask) {
    Write-Host "OK: legacy task is Disabled." -ForegroundColor Green
}

Write-Section 'Running watcher processes (Win32_Process)'
$allProcesses = Get-CimInstance Win32_Process
$watcherProcesses = $allProcesses | Where-Object {
    $_.Name -match 'ReadyJobsWatcher' -or
    ($_.CommandLine -and $_.CommandLine -match 'ready_jobs_watcher')
}

if ($watcherProcesses) {
    $watcherProcesses |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine |
        Format-List | Out-String | Write-Host
} else {
    Write-Host 'No Ready Jobs Watcher processes currently running.' -ForegroundColor Yellow
}

$watcherCount = if ($watcherProcesses) { @($watcherProcesses).Count } else { 0 }
Write-Host "Watcher process count: $watcherCount"
if ($watcherCount -gt 1) {
    Write-Host 'WARNING: more than one watcher process is running.' -ForegroundColor Red
} elseif ($watcherCount -eq 1) {
    Write-Host 'OK: exactly one watcher process is running.' -ForegroundColor Green
} else {
    Write-Host 'No watcher process running right now (this is not necessarily a problem).' -ForegroundColor Yellow
}

Write-Section "Diagnostic PID file: $DiagnosticPidPath"
if (Test-Path -LiteralPath $DiagnosticPidPath) {
    $pidContent = Get-Content -LiteralPath $DiagnosticPidPath -Raw
    Write-Host "Content: $pidContent"
} else {
    Write-Host 'Diagnostic PID file not present.' -ForegroundColor Yellow
}

Write-Section 'Summary'
Write-Host "Supported task '$SupportedTaskName' state: $(if ($supportedTask) { $supportedTask.State } else { '<not found>' })"
Write-Host "Legacy task '$LegacyTaskName' state: $(if ($legacyTask) { $legacyTask.State } else { '<not found>' })"
Write-Host "Watcher processes running: $watcherCount"
Write-Host ''
Write-Host 'This script is read-only. It made no changes to Task Scheduler, running processes, or files.'
