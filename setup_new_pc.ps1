# BMRI / IHSG monitor - one-time setup on a new Windows PC
# Usage: right-click this file -> "Run with PowerShell"
#   1. creates a Python environment next to this folder and installs the packages
#   2. registers the 3 scheduled tasks (08:00 brief, 16:20 close report, 18:00 broksum update)
#   3. switches email alerts on and sends one test email

$ErrorActionPreference = "Stop"
$monitor = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $monitor
$venv = Join-Path $root ".venv"

Write-Host "== BMRI monitor setup ==" -ForegroundColor Cyan
Write-Host "Folder: $monitor"

# 1. Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python not found. Install Python 3.11+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
& python --version

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating Python environment..."
    & python -m venv $venv
}
$venvPython = Join-Path $venv "Scripts\python.exe"
$venvPythonw = Join-Path $venv "Scripts\pythonw.exe"
Write-Host "Installing packages (pandas, matplotlib, python-pptx, flask, pywebview)..."
& $venvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $monitor "requirements.txt")

# 2. Scheduled tasks (Mon-Fri)
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
$tasks = @(
    @{ Name = "BMRI Morning Brief";  Time = "08:00"; Script = "bmri_morning_brief.py"; Args = "" },
    @{ Name = "BMRI Daily Check";    Time = "16:20"; Script = "bmri_daily_check.py";   Args = "--no-ask" },
    @{ Name = "BMRI Broksum Update"; Time = "18:00"; Script = "bmri_daily_check.py";   Args = "--no-wait" }
)
foreach ($task in $tasks) {
    $arguments = "`"$monitor\$($task.Script)`" $($task.Args)".Trim()
    $action = New-ScheduledTaskAction -Execute $venvPythonw -Argument $arguments -WorkingDirectory $monitor
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $task.Time
    Register-ScheduledTask -TaskName $task.Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host ("Scheduled: {0,-20} {1}" -f $task.Name, $task.Time)
}

# Shortcuts for manual runs
@"
@echo off
cd /d "$monitor"
"$venvPython" bmri_daily_check.py %*
pause
"@ | Set-Content -Encoding ascii (Join-Path $monitor "run_close_report.bat")
@"
@echo off
cd /d "$monitor"
"$venvPython" bmri_morning_brief.py %*
pause
"@ | Set-Content -Encoding ascii (Join-Path $monitor "run_morning_brief.bat")

# Dashboard app shortcuts (Start Menu always, Desktop if you say yes)
$launcher = Join-Path $monitor "Trading Monitor.vbs"
$wsh = New-Object -ComObject WScript.Shell
function New-MonitorShortcut($linkPath) {
    $link = $wsh.CreateShortcut($linkPath)
    $link.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $link.Arguments = "`"$launcher`""
    $link.WorkingDirectory = $monitor
    $link.IconLocation = "$env:WINDIR\System32\shell32.dll,43"
    $link.Description = "BMRI / IHSG Trading Monitor dashboard"
    $link.Save()
}
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Trading Monitor.lnk"
New-MonitorShortcut $startMenu
Write-Host "Start Menu shortcut: $startMenu"
# Main desktop app: IDX Investing Monitor (portfolio, watchlist, market, ideas)
$appExe = Join-Path $monitor "IDX Monitor.exe"
$appLink = $wsh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Programs")) "IDX Investing Monitor.lnk"))
$appLink.TargetPath = $appExe
$appLink.WorkingDirectory = $monitor
$appLink.IconLocation = (Join-Path $monitor "app_icon.ico")
$appLink.Description = "IDX Investing Monitor - portfolio, dividend watchlist, market, macro ideas"
$appLink.Save()
Write-Host "Start Menu shortcut: IDX Investing Monitor (pin it to the taskbar from the Start Menu)"
$answer = Read-Host "Also put a 'Trading Monitor' shortcut on your Desktop? (y/n)"
if ($answer -match "^[Yy]") {
    $desktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "Trading Monitor.lnk"
    New-MonitorShortcut $desktopLink
    Write-Host "Desktop shortcut: $desktopLink"
}

# 3. Email on + test
$configPath = Join-Path $monitor "alert_config.json"
$config = Get-Content $configPath -Raw | ConvertFrom-Json
$config.email.enabled = $true
$config | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $configPath
Write-Host "Sending a test email..."
Push-Location $monitor
& $venvPython send_test_email.py
Pop-Location

Write-Host ""
Write-Host "Done. Check your inbox (and Spam) for the test email." -ForegroundColor Green
Write-Host "Open the dashboard: Start Menu -> Trading Monitor (or double-click Trading Monitor.vbs)."
Write-Host "If the test FAILED, email was left on - tell the assistant the error text."
Read-Host "Press Enter to close"
