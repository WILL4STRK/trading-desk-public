# L1 learner (docs/MEME.md section 18). Paper only: the learner reads the desk's files and never writes them.
#
#   run_learner.ps1          the runner: keeps `learner.py run` going, restarts it 10 s after any exit. One runner at a time
#                            (named mutex, plus a stale-PID check of state\learner.pid; learner.py itself holds an OS lock
#                            on state\learner.lock, so two learners can never write the same logs).
#   run_learner.ps1 -Watch   the watchdog: one check, one line in C:\Trading\logs\learner-watchdog.log, then exit.
#
# Runs ONLY from the pinned clone C:\Trading\learner\repo (detached at the merged commit), never from the live folder.
param([switch]$Watch)
$ErrorActionPreference = 'Continue'
$repo    = 'C:\Trading\learner\repo'
$lhome   = 'C:\Trading\research\learner'
$state   = Join-Path $lhome 'state'
$runLog  = Join-Path $lhome 'learner.log'
$outLog  = Join-Path $lhome 'learner.out.log'
$watchF  = Join-Path $state 'watch.json'
$alertLog = 'C:\Trading\logs\learner-watchdog.log'
$shadowsF = 'C:\Users\Willr\trading-desk\data\accounts\meme\shadows.jsonl'
$predsF   = Join-Path $lhome 'out\predictions.jsonl'
$pidF     = Join-Path $state 'learner.pid'
$stallMin = 90          # predictions.jsonl not grown for this long while shadows.jsonl grew: ALERT
$beatMin  = 5           # learner heartbeat older than this: ALERT
New-Item -ItemType Directory -Path $state, (Split-Path $alertLog) -Force | Out-Null

function Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + 'Z' }

# A file's true length, even while another process holds it open (LastWriteTime/Length can lag on Windows).
function Get-Len($p) {
    if (-not (Test-Path $p)) { return [long]0 }
    try { $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite,Delete'); try { return [long]$fs.Length } finally { $fs.Dispose() } }
    catch { return [long]-1 }
}

function Get-LearnerPid {
    if (-not (Test-Path $pidF)) { return $null }
    try {
        $fs = [IO.File]::Open($pidF, 'Open', 'Read', 'ReadWrite,Delete')
        try { $txt = (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Dispose() }
        $n = 0; if ([int]::TryParse($txt.Trim(), [ref]$n)) { return $n }
    } catch {}
    return $null
}

function Test-LearnerAlive($procId) {
    if (-not $procId) { return $false }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    return [bool]($p -and $p.CommandLine -like '*learner.py*')
}

function Invoke-Watch {
    $now = (Get-Date).ToUniversalTime()
    $alerts = @()
    $w = [ordered]@{ sh_len = -1; sh_grew = $now.ToString('o'); pr_len = -1; pr_grew = $now.ToString('o'); missed = -1 }
    if (Test-Path $watchF) {
        try { $o = Get-Content $watchF -Raw | ConvertFrom-Json
              $w.sh_len = [long]$o.sh_len; $w.sh_grew = [string]$o.sh_grew; $w.pr_len = [long]$o.pr_len
              $w.pr_grew = [string]$o.pr_grew; $w.missed = [long]$o.missed } catch {}
    }
    $sh = Get-Len $shadowsF; $pr = Get-Len $predsF
    if ($sh -ne $w.sh_len) { $w.sh_grew = $now.ToString('o'); $w.sh_len = $sh }
    if ($pr -ne $w.pr_len) { $w.pr_grew = $now.ToString('o'); $w.pr_len = $pr }
    $shGrew = [datetime]::Parse($w.sh_grew).ToUniversalTime(); $prGrew = [datetime]::Parse($w.pr_grew).ToUniversalTime()
    $prAge = [math]::Round(($now - $prGrew).TotalMinutes)
    if ($prAge -ge $stallMin -and $shGrew -gt $prGrew) {
        $alerts += "predictions.jsonl not grown for $prAge min while shadows.jsonl grew"
    }
    $st = $null
    try { $st = Get-Content (Join-Path $state 'state.json') -Raw | ConvertFrom-Json } catch {}
    $pid0 = Get-LearnerPid
    $alive = Test-LearnerAlive $pid0
    if (-not $alive) { $alerts += "learner not running (lock pid $pid0)" }
    $beatAge = ''
    if ($st -and $st.heartbeat) {
        $beatAge = [math]::Round(([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$st.heartbeat) / 60, 1)
        if ($beatAge -gt $beatMin) { $alerts += "learner heartbeat $beatAge min old" }
    } elseif ($alive) { $alerts += 'learner running but no state.json heartbeat yet' }
    if ($st -and $st.error) { $alerts += "learner error: $($st.error)" }
    $missed = if ($st) { [long]$st.counts.missed } else { -1 }
    if ($w.missed -ge 0 -and $missed -gt $w.missed) { $alerts += "missed shadows +$($missed - $w.missed) (first seen closed)" }
    $w.missed = $missed
    $c = if ($st) { $st.counts } else { $null }
    $line = "{0} {1} pid={2} alive={3} beat_min={4} predicted={5} closes={6} missed={7} void={8} scored={9} shadows_bytes={10} predictions_bytes={11} predictions_idle_min={12}" -f `
        (Stamp), $(if ($alerts) { 'ALERT' } else { 'OK' }), $pid0, $alive, $beatAge, $c.predicted, $c.closes, $c.missed, $c.void, $c.scored, $sh, $pr, $prAge
    if ($alerts) { $line += ' | ALERT: ' + ($alerts -join '; ') }
    Add-Content -Path $alertLog -Value $line -Encoding UTF8
    $w | ConvertTo-Json | Set-Content -Path "$watchF.tmp" -Encoding UTF8
    Move-Item -LiteralPath "$watchF.tmp" -Destination $watchF -Force      # atomic replace
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
    Add-Content $runLog ("{0} runner: {1} is on a branch; pin it to the merged commit (git checkout --detach <sha>)" -f (Stamp), $repo); exit 1
}
$mutex = New-Object System.Threading.Mutex($false, 'Global\L1LearnerRunner')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    $old = Get-LearnerPid
    if (Test-LearnerAlive $old) {
        Add-Content $runLog ("{0} runner: learner already running as pid {1} outside this runner; exiting" -f (Stamp), $old); exit 0
    }
    if ($old) { Add-Content $runLog ("{0} runner: stale lock pid {1} (not running); starting" -f (Stamp), $old) }
    Set-Location $repo
    $env:PYTHONUTF8 = '1'
    $head = (& git -C $repo rev-parse --short HEAD)
    while ($true) {
        Add-Content $runLog ("{0} runner: start learner at {1}" -f (Stamp), $head)
        & py -3.13 -X utf8 (Join-Path $repo 'research\learner\learner.py') run *>> $outLog
        Add-Content $runLog ("{0} runner: learner exited with {1}; restart in 10 s" -f (Stamp), $LASTEXITCODE)
        Start-Sleep -Seconds 10
    }
} finally { $mutex.ReleaseMutex() }
