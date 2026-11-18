# Trading Desk on a Windows PC that stays on: installs what it needs, starts the desk
# hidden at every log-on (and now), and opens a free Cloudflare quick tunnel so you
# can sign in from another computer. Safe to run again: it updates the code and the task.
#
#   irm https://raw.githubusercontent.com/WILL4STRK/trading-desk-public/desk-upgrades/tools/windows/setup.ps1 | iex
#
# The desk still listens on 127.0.0.1 only. cloudflared connects out to Cloudflare and
# gets a https://<random>.trycloudflare.com address; every visit through it must sign
# in with your desk password. The address changes when the tunnel restarts: the desk
# pushes each new one to your phone (ntfy) and its event log.

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

Write-Host "== 1/5 Tools"
Need { Get-Command git -ErrorAction SilentlyContinue } "Git.Git" "Git"
Need { Get-Command py -ErrorAction SilentlyContinue } "Python.Python.3.12" "Python"
Need { (Get-Command cloudflared -ErrorAction SilentlyContinue) -or (Test-Path "C:\Program Files (x86)\cloudflared\cloudflared.exe") } "Cloudflare.cloudflared" "cloudflared"

Write-Host "== 2/5 Code in $Dir"
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

Write-Host "== 3/5 Remote sign-in"
if (-not (Test-Path (Join-Path $Dir "data\remote.json"))) {
    Write-Host "Choose the password you will type to open the desk from another computer."
    Push-Location $Dir; py -3 -m desk password; Pop-Location
}
[Environment]::SetEnvironmentVariable("DESK_HOSTS", ".trycloudflare.com", "User")   # tunnel names, sign-in required
$env:DESK_HOSTS = ".trycloudflare.com"

Write-Host "== 4/5 Start at log-on, hidden"
$action   = New-ScheduledTaskAction -Execute $Pyw -Argument "-m desk --port $Port --no-browser --tunnel" -WorkingDirectory $Dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue   # a rerun picks up new code
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force   # its tunnel ends with the desk
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $Task

Write-Host "== 5/5 Check"
Start-Sleep -Seconds 8
try {
    $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/state" -TimeoutSec 20
    Write-Host "The desk is running."
} catch {
    Write-Host "The desk did not answer yet. Run this again, or check Task Scheduler > $Task."
}
Write-Host ""
Start-Sleep -Seconds 10
try {
    $url = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/state" -TimeoutSec 20 | ConvertFrom-Json).remote_url
} catch { $url = $null }
if ($url) { Write-Host "Open it from any computer and sign in:  $url" }
else { Write-Host "The tunnel address is not ready yet. It will arrive as a phone alert (set an ntfy topic) and in the desk's event log." }
Write-Host "Keep this PC awake: Settings > System > Power > Sleep = Never."
