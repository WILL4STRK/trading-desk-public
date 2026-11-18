# Trading Desk on a Windows PC that stays on: installs what it needs, starts the desk
# hidden at every log-on (and now), and shares it privately with your other devices
# through Tailscale Serve. Safe to run again: it updates the code and the task.
#
#   irm https://raw.githubusercontent.com/WILL4STRK/trading-desk-public/desk-upgrades/tools/windows/setup.ps1 | iex
#
# The desk still listens on 127.0.0.1 only. Tailscale Serve forwards your tailnet's
# HTTPS address to it, so only devices signed in to your Tailscale account reach it.

$ErrorActionPreference = "Stop"
$Repo   = "https://github.com/WILL4STRK/trading-desk-public.git"
$Branch = "desk-upgrades"
$Dir    = Join-Path $env:USERPROFILE "trading-desk"
$Port   = 8790
$Task   = "Trading Desk"
$TsExe  = "C:\Program Files\Tailscale\tailscale.exe"

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
Need { Test-Path $TsExe } "Tailscale.Tailscale" "Tailscale"

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

Write-Host "== 3/5 Tailscale"
$st = & $TsExe status --json | ConvertFrom-Json
if ($st.BackendState -ne "Running") {
    Write-Host "Sign in to Tailscale in the browser window that opens (the same account as your Mac)."
    & $TsExe up
    $st = & $TsExe status --json | ConvertFrom-Json
}
$Name = $st.Self.DNSName.TrimEnd(".")
& $TsExe serve --bg $Port
[Environment]::SetEnvironmentVariable("DESK_HOSTS", $Name, "User")   # the desk lets this name in
$env:DESK_HOSTS = $Name

Write-Host "== 4/5 Start at log-on, hidden"
$action   = New-ScheduledTaskAction -Execute $Pyw -Argument "-m desk --port $Port --no-browser" -WorkingDirectory $Dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue   # a rerun picks up new code
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
Write-Host "Open it on your Mac (Tailscale on, same account):  https://$Name/"
Write-Host "Keep this PC awake: Settings > System > Power > Sleep = Never."
