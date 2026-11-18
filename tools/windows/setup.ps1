# Trading Desk on a Windows PC that stays on: installs what it needs and starts the desk
# hidden at every log-on (and now). Safe to run again: it updates the code and the task.
#
#   irm https://raw.githubusercontent.com/WILL4STRK/trading-desk-public/desk-upgrades/tools/windows/setup.ps1 | iex
#
# The desk listens on 127.0.0.1 only: open it on this PC, or from elsewhere through
# Chrome Remote Desktop into this PC. Nothing is shared to the network.

$ErrorActionPreference = "Stop"
$Repo   = "https://github.com/WILL4STRK/trading-desk-public.git"
$Branch = "desk-upgrades"
$Dir    = Join-Path $env:USERPROFILE "trading-desk"
$Port   = 8790
$Task   = "Trading Desk"

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need($Test, $Id, $Name) {
    if (& $Test) { return }
    Write-Host "Installing $Name..."
    winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (& $Test)) { throw "$Name did not install. Install it by hand, then run this again." }
}

Write-Host "== 1/4 Tools"
Need { Get-Command git -ErrorAction SilentlyContinue } "Git.Git" "Git"
Need { Get-Command py -ErrorAction SilentlyContinue } "Python.Python.3.12" "Python"

Write-Host "== 2/4 Code in $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
    git -C $Dir fetch --quiet origin
    git -C $Dir checkout --quiet $Branch
    git -C $Dir pull --quiet --ff-only origin $Branch
} else {
    git clone --quiet --branch $Branch $Repo $Dir
}
# Windows has no time zone database of its own; the desk needs New York time.
py -3 -m pip install --user --quiet --upgrade tzdata
$Py  = (py -3 -c "import sys; print(sys.executable)").Trim()
$Pyw = Join-Path (Split-Path $Py) "pythonw.exe"

# Optional: your record from the Mac, zipped as trading-desk-data.zip (holds data\...).
$Zip = Join-Path $env:USERPROFILE "Downloads\trading-desk-data.zip"
if ((Test-Path $Zip) -and -not (Test-Path (Join-Path $Dir "data\journal.db"))) {
    Write-Host "Copying in your record from $Zip"
    Expand-Archive -Path $Zip -DestinationPath $Dir -Force
}

[Environment]::SetEnvironmentVariable("DESK_HOSTS", $null, "User")   # left by an older setup that shared the desk

Write-Host "== 3/4 Start at log-on, hidden"
$action   = New-ScheduledTaskAction -Execute $Pyw -Argument "-m desk --port $Port --no-browser" -WorkingDirectory $Dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue   # a rerun picks up new code
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $Task

Write-Host "== 4/4 Check"
Start-Sleep -Seconds 8
try {
    $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/state" -TimeoutSec 20
    Write-Host "The desk is running."
} catch {
    Write-Host "The desk did not answer yet. Run this again, or check Task Scheduler > $Task."
}
Write-Host ""
Write-Host "Open it on this PC:  http://127.0.0.1:$Port/   (away from home: Chrome Remote Desktop into this PC)"
Write-Host "Keep this PC awake: Settings > System > Power > Sleep = Never."
