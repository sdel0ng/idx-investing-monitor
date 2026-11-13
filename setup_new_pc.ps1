# IDX Investing Monitor - one-time setup on a new Windows PC
# Usage: right-click this file -> "Run with PowerShell"
#   1. creates a Python environment (.venv next to this folder) and installs the packages
#   2. creates Start Menu (and optional Desktop) shortcuts for the IDX Investing Monitor app
#   3. registers the scheduled reports (08:00 morning brief, 16:20 close report, 18:00 update)
#   4. optional: e-mail alerts (only if alert_config.json has your Gmail app password)

$ErrorActionPreference = "Stop"
$monitor = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $monitor
$venv = Join-Path $root ".venv"

Write-Host "== IDX Investing Monitor setup ==" -ForegroundColor Cyan
Write-Host "App folder: $monitor"
Write-Host "Python environment: $venv"

# 1. Python + packages
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python not found. Install Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "(tick 'Add python.exe to PATH' in the installer), then run this again." -ForegroundColor Red
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
Write-Host "Installing packages (flask, pywebview, pandas, matplotlib, python-pptx)... this takes 1-3 minutes"
& $venvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $monitor "requirements.txt")

# Personal files: start empty if they are not here yet (copy your own from the transfer zip to keep your data)
if (-not (Test-Path (Join-Path $monitor "alert_config.json"))) {
    Copy-Item (Join-Path $monitor "alert_config.example.json") (Join-Path $monitor "alert_config.json")
    Write-Host "Created alert_config.json from the template (e-mail alerts OFF)."
}

# 2. Shortcuts for the app
$wsh = New-Object -ComObject WScript.Shell
$appVbs = Join-Path $monitor "IDX Investing Monitor.vbs"
$appExe = Join-Path $monitor "IDX Monitor.exe"
function New-AppShortcut($linkPath) {
    $link = $wsh.CreateShortcut($linkPath)
    if (Test-Path $appExe) {
        $link.TargetPath = $appExe
    } else {
        $link.TargetPath = "$env:WINDIR\System32\wscript.exe"
        $link.Arguments = "`"$appVbs`""
    }
    $link.WorkingDirectory = $monitor
    $link.IconLocation = (Join-Path $monitor "app_icon.ico")
    $link.Description = "IDX Investing Monitor - portfolio, screener, charts, plans, macro"
    $link.Save()
}
New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Programs")) "IDX Investing Monitor.lnk")
Write-Host "Start Menu shortcut: IDX Investing Monitor (you can pin it to the taskbar)" -ForegroundColor Green
$answer = Read-Host "Also put an 'IDX Investing Monitor' shortcut on your Desktop? (y/n)"
if ($answer -match "^[Yy]") {
    New-AppShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "IDX Investing Monitor.lnk")
    Write-Host "Desktop shortcut created." -ForegroundColor Green
}

# 3. Scheduled reports (Mon-Fri)
$answer = Read-Host "Schedule the daily reports (08:00 morning brief, 16:20 close report, 18:00 update)? (y/n)"
if ($answer -match "^[Yy]") {
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
}

# 4. Optional e-mail test (only when a real app password is filled in)
$configPath = Join-Path $monitor "alert_config.json"
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if ($config.email -and $config.email.app_password -and $config.email.app_password -notlike "xxxx*") {
    $answer = Read-Host "Turn on e-mail alerts and send a test e-mail? (y/n)"
    if ($answer -match "^[Yy]") {
        $config.email.enabled = $true
        $config | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $configPath
        Push-Location $monitor
        & $venvPython send_test_email.py
        Pop-Location
    }
} else {
    Write-Host "E-mail alerts: off (to use them, put your Gmail app password in alert_config.json and run this again)."
}

Write-Host ""
Write-Host "Done. Open the app: Start Menu -> IDX Investing Monitor (or double-click 'IDX Investing Monitor.vbs')." -ForegroundColor Green
Read-Host "Press Enter to close"
