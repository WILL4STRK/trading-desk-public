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
# Windows PowerShell 5.1 does not stop on a failed native command: check its exit code.
function Run([scriptblock]$Cmd, $What) {
    & $Cmd
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE). Fix that, then run this again." }
}
function Python-Ok {
    try { & py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null; return $LASTEXITCODE -eq 0 }
    catch { return $false }
}
function Need($Test, $Id, $Name) {
    if (& $Test) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Name is missing and winget is not available. Install 'App Installer' from the Microsoft Store (or install $Name by hand), then run this again."
    }
    Write-Host "Installing $Name..."
    Run { winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements } "Installing $Name"
    Refresh-Path
    if (-not (& $Test)) { throw "$Name did not install. Install it by hand, then run this again." }
}

Write-Host "== 1/5 Tools"
Need { Get-Command git -ErrorAction SilentlyContinue } "Git.Git" "Git"
Need { Python-Ok } "Python.Python.3.12" "Python 3.9 or newer"

Write-Host "== 2/5 Stop the running desk and anything an older setup shared"
if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Task
    for ($i = 0; $i -lt 30 -and (Get-ScheduledTask -TaskName $Task).State -eq "Running"; $i++) { Start-Sleep -Seconds 1 }
    if ((Get-ScheduledTask -TaskName $Task).State -eq "Running") { throw "The desk task did not stop. End it in Task Scheduler, then run this again." }
}
# An older setup could leave a tunnel or a Tailscale Serve mapping pointing at the desk's port.
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
    Where-Object { $_.CommandLine -match "(127\.0\.0\.1|localhost):$Port\b" } |
    ForEach-Object { Write-Host "Stopping old tunnel (pid $($_.ProcessId))"; Stop-Process -Id $_.ProcessId -Force }
$Ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $Ts) {
    try {   # Tailscale signed out answers on stderr ("netMap is nil"): nothing is being shared then
        $serve = (& $Ts serve status 2>&1 | Out-String)
        if ($serve -match ":$Port\b") { Write-Host "Removing old Tailscale Serve mapping"; & $Ts serve reset 2>&1 | Out-Null }
    } catch { }
}
[Environment]::SetEnvironmentVariable("DESK_HOSTS", $null, "User")
Remove-Item Env:DESK_HOSTS -ErrorAction SilentlyContinue

Write-Host "== 3/5 Code in $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
    Run { git -C $Dir fetch --quiet origin } "git fetch"
    Run { git -C $Dir checkout --quiet $Branch } "git checkout"
    Run { git -C $Dir pull --quiet --ff-only origin $Branch } "git pull"
} else {
    Run { git clone --quiet --branch $Branch $Repo $Dir } "git clone"
}
# Windows has no time zone database of its own; the desk needs New York time.
Run { py -3 -m pip install --user --quiet --upgrade tzdata } "Installing tzdata"
Run { py -3 -c "import zoneinfo; zoneinfo.ZoneInfo('America/New_York')" } "Checking New York time"
$Py = (py -3 -c "import sys; print(sys.executable)")
if ($LASTEXITCODE -ne 0) { throw "Could not find Python's folder." }
$Pyw = Join-Path (Split-Path $Py.Trim()) "pythonw.exe"
if (-not (Test-Path $Pyw)) { throw "pythonw.exe is missing at $Pyw. Reinstall Python, then run this again." }

# Optional: your record from the Mac, zipped as trading-desk-data.zip (holds data\...).
# Only into an empty record: an existing one is never overwritten.
$Zip  = Join-Path $env:USERPROFILE "Downloads\trading-desk-data.zip"
$Data = Join-Path $Dir "data"
if (Test-Path $Zip) {
    if (-not (Test-Path $Data) -or -not (Get-ChildItem $Data -Force | Select-Object -First 1)) {
        Write-Host "Copying in your record from $Zip"
        Expand-Archive -Path $Zip -DestinationPath $Dir -Force
    } else {
        Write-Host "Not copying $Zip`: this PC already has a record in $Data. Move that folder away first if you want to replace it."
    }
}

Write-Host "== 4/5 Start at log-on, hidden"
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { throw "Port $Port is already in use by process $($busy[0].OwningProcess). Close that program, then run this again." }
$action   = New-ScheduledTaskAction -Execute $Pyw -Argument "-m desk --port $Port --no-browser" -WorkingDirectory $Dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $Task

Write-Host "== 5/5 Check"
$ok = $false
for ($i = 0; $i -lt 45 -and -not $ok; $i++) {
    Start-Sleep -Seconds 1
    $l = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $l) { continue }
    if ($l.LocalAddress -ne "127.0.0.1") { throw "Port $Port is listening on $($l.LocalAddress), not 127.0.0.1 only. Stop and report this." }
    if ((Get-Process -Id $l.OwningProcess).Path -ne $Pyw) { throw "Something other than the desk answers on port $Port." }
    try { $ok = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/state" -TimeoutSec 10).StatusCode -eq 200 } catch { }
}
if (-not $ok) { throw "The desk did not start. Check Task Scheduler > $Task, then run this again." }
Write-Host "The desk is running on 127.0.0.1:$Port only."
Write-Host ""
Write-Host "Open it on this PC:  http://127.0.0.1:$Port/   (away from home: Chrome Remote Desktop into this PC)"
Write-Host "Keep this PC awake: Settings > System > Power > Sleep = Never."
