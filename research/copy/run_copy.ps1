# C1 / C1-S copy follower (docs/MEME.md section 19). Paper only: no wallet, no signing key, no order path.
#
#   run_copy.ps1          the runner: keeps `follow.py run` going, restarts it 10 s after any exit. One runner at a time
#                         (named mutex, plus a stale-PID check of follow.pid; follow.py itself holds an OS lock on
#                         follow.lock, so two followers can never write the same logs).
#   run_copy.ps1 -Watch   the watchdog: one check, one line in C:\Trading\logs\copy-watchdog.log, then exit.
#
# Runs ONLY from the pinned clone C:\Trading\copy\repo (detached at a commit), never from the live desk folder.
param([switch]$Watch)
$ErrorActionPreference = 'Continue'
$repo     = 'C:\Trading\copy\repo'
$chome    = 'C:\Trading\research\copy'
$runLog   = Join-Path $chome 'runner.log'
$outLog   = Join-Path $chome 'follow.out.log'
$alertLog = 'C:\Trading\logs\copy-watchdog.log'
$pidF     = Join-Path $chome 'follow.pid'
$beatMin  = 5           # follower heartbeat (state.json) older than this: ALERT
New-Item -ItemType Directory -Path $chome, (Split-Path $alertLog) -Force | Out-Null

function Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + 'Z' }

function Read-Shared($p) {
    if (-not (Test-Path $p)) { return $null }
    try { $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite,Delete')
          try { return (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Dispose() } }
    catch { return $null }
}

function Get-FollowPid {
    $txt = Read-Shared $pidF
    $n = 0; if ($txt -and [int]::TryParse($txt.Trim(), [ref]$n)) { return $n }
    return $null
}

function Test-FollowAlive($procId) {
    if (-not $procId) { return $false }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    return [bool]($p -and $p.CommandLine -like '*follow.py*')
}

function Invoke-Watch {
    $alerts = @()
    $pid0 = Get-FollowPid
    $alive = Test-FollowAlive $pid0
    if (-not $alive) { $alerts += "follower not running (pid file $pid0)" }
    $st = $null
    try { $st = (Read-Shared (Join-Path $chome 'state.json')) | ConvertFrom-Json } catch {}
    $beatAge = ''
    if ($st -and $st.heartbeat) {
        $beatAge = [math]::Round(([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$st.heartbeat) / 60, 1)
        if ($beatAge -gt $beatMin) { $alerts += "heartbeat $beatAge min old" }
    } elseif ($alive) { $alerts += 'running but no state.json heartbeat yet' }
    $c = if ($st) { $st.counts } else { $null }
    $open = if ($st) { @($st.positions.PSObject.Properties | Where-Object { -not $_.Value.closed }).Count } else { -1 }
    $line = "{0} {1} pid={2} alive={3} beat_min={4} buys={5} closes={6} missed={7} open={8} credits={9}" -f `
        (Stamp), $(if ($alerts) { 'ALERT' } else { 'OK' }), $pid0, $alive, $beatAge, $c.buys, $c.closes, $c.missed, $open, $st.credits
    if ($alerts) { $line += ' | ALERT: ' + ($alerts -join '; ') }
    Add-Content -Path $alertLog -Value $line -Encoding UTF8
    $line
}

if ($Watch) { Invoke-Watch; exit 0 }

# ---- runner
$here = (Resolve-Path $PSScriptRoot).Path
if (-not $here.StartsWith($repo, [StringComparison]::OrdinalIgnoreCase)) {
    Add-Content $runLog ("{0} runner: refusing to run from {1}; run from {2}" -f (Stamp), $here, $repo); exit 1
}
& git -C $repo symbolic-ref -q HEAD *> $null
if ($LASTEXITCODE -eq 0) {
    Add-Content $runLog ("{0} runner: {1} is on a branch; pin it to a commit (git checkout --detach <sha>)" -f (Stamp), $repo); exit 1
}
$mutex = New-Object System.Threading.Mutex($false, 'Global\C1CopyRunner')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    $old = Get-FollowPid
    if (Test-FollowAlive $old) {
        Add-Content $runLog ("{0} runner: follower already running as pid {1} outside this runner; exiting" -f (Stamp), $old); exit 0
    }
    if ($old) { Add-Content $runLog ("{0} runner: stale pid {1} (not running); starting" -f (Stamp), $old) }
    Set-Location $repo
    $env:PYTHONUTF8 = '1'
    $head = (& git -C $repo rev-parse --short HEAD)
    while ($true) {
        Add-Content $runLog ("{0} runner: start follower at {1}" -f (Stamp), $head)
        & py -3.13 -X utf8 (Join-Path $repo 'research\copy\follow.py') run *>> $outLog
        Add-Content $runLog ("{0} runner: follower exited with {1}; restart in 10 s" -f (Stamp), $LASTEXITCODE)
        Start-Sleep -Seconds 10
    }
} finally { $mutex.ReleaseMutex() }
