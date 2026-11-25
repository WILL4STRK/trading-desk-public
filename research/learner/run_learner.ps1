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
# The L1_* environment variables exist only for the tests (temp folders); the scheduled tasks never set them.
$lhome   = if ($env:L1_HOME) { $env:L1_HOME } else { 'C:\Trading\research\learner' }
$state   = Join-Path $lhome 'state'
$runLog  = Join-Path $lhome 'learner.log'
$outLog  = Join-Path $lhome 'learner.out.log'
$watchF  = Join-Path $state 'watch.json'
$alertLog = if ($env:L1_ALERT_LOG) { $env:L1_ALERT_LOG } else { 'C:\Trading\logs\learner-watchdog.log' }
$deskDir  = if ($env:L1_DESK_MEME) { $env:L1_DESK_MEME } else { 'C:\Users\Willr\trading-desk\data\accounts\meme' }
$modelF   = Join-Path $deskDir 'model.json'
$predsF   = Join-Path $lhome 'out\predictions.jsonl'
$pidF     = Join-Path $state 'learner.pid'
$t18      = 1795411987  # docs/MEME.md section 18 start (learner.py T18): only opens after it are L1's
$openMin  = 10          # a policy-all, screen-pass open this old in model.json with no prediction: ALERT
$beatMin  = 5           # learner heartbeat older than this: ALERT
New-Item -ItemType Directory -Path $state, (Split-Path $alertLog) -Force | Out-Null

function Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + 'Z' }

# A file's true length, even while another process holds it open (LastWriteTime/Length can lag on Windows).
function Get-Len($p) {
    if (-not (Test-Path $p)) { return [long]0 }
    try { $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite,Delete'); try { return [long]$fs.Length } finally { $fs.Dispose() } }
    catch { return [long]-1 }
}

# Every file the watchdog reads goes through this: shared read, write AND delete, closed at once. Never Get-Content.
# Note: on Windows an open handle still makes another process's replace of the file fail, even when it shares delete
# (measured); the open is kept to one short read, and the writers retry their replace (learner atomic_write, desk write_json).
function Read-Shared($p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    try {
        $fs = [IO.File]::Open($p, 'Open', 'Read', 'ReadWrite,Delete')
        try { return (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd() } finally { $fs.Dispose() }
    } catch { return $null }
}

function Get-LearnerPid {
    $txt = Read-Shared $pidF
    if ($null -eq $txt) { return $null }
    $n = 0; if ([int]::TryParse($txt.Trim(), [ref]$n)) { return $n }
    return $null
}

# Policy-all, screen-pass shadows opened after T18 and open in model.json for $openMin+ minutes, with no line in
# predictions.jsonl: the learner polls every 30 s, so each should have one. (One that opened and closed between polls goes
# to the learner's missed count, alerted separately.) Returns @(opens, without_prediction) or $null if model.json is unreadable.
function Get-OpenLag {
    $mj = Read-Shared $modelF
    if (-not $mj) { return $null }
    try { $m = $mj | ConvertFrom-Json } catch { return $null }
    $preds = @{}
    $pt = Read-Shared $predsF
    if ($pt) {
        foreach ($l in ($pt -split "`n")) {
            $k = [regex]::Match($l, '"key"\s*:\s*"([^"]+)"'); $e = [regex]::Match($l, '"entry_ts"\s*:\s*([-0-9.eE+]+)')
            if ($k.Success -and $e.Success) { $preds[('{0}|{1:R}' -f $k.Groups[1].Value, [double]$e.Groups[1].Value)] = 1 }
        }
    }
    $nowUnix = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $opens = 0; $lag = 0
    foreach ($s in @($m.shadows)) {
        if ($s -and $s.policy -eq 'all' -and $s.screen -eq 'pass' -and $null -ne $s.ts -and [double]$s.ts -gt $t18) {
            if (($nowUnix - [double]$s.ts) -lt $openMin * 60) { continue }
            $opens++
            if (-not $preds.ContainsKey(('{0}|{1:R}' -f $s.key, [double]$s.ts))) { $lag++ }
        }
    }
    return @($opens, $lag)
}

function Test-LearnerAlive($procId) {
    if (-not $procId) { return $false }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    return [bool]($p -and $p.CommandLine -like '*learner.py*')
}

function Invoke-Watch {
    $now = (Get-Date).ToUniversalTime()
    $alerts = @()
    $w = [ordered]@{ pr_len = -1; pr_grew = $now.ToString('o'); missed = -1 }
    $wt = Read-Shared $watchF
    if ($wt) {
        try { $o = $wt | ConvertFrom-Json
              $w.pr_len = [long]$o.pr_len; $w.pr_grew = [string]$o.pr_grew; $w.missed = [long]$o.missed } catch {}
    }
    $pr = Get-Len $predsF
    if ($pr -ne $w.pr_len) { $w.pr_grew = $now.ToString('o'); $w.pr_len = $pr }
    $prAge = [math]::Round(($now - [datetime]::Parse($w.pr_grew).ToUniversalTime()).TotalMinutes)    # shown in the line only
    # predictions against OPENS (model.json), never against shadows.jsonl growth: that file gets closes of every policy
    $lagInfo = Get-OpenLag
    $opens = ''; $noPred = ''
    if ($null -ne $lagInfo) {
        $opens = $lagInfo[0]; $noPred = $lagInfo[1]
        if ($noPred -gt 0) { $alerts += "$noPred policy-all screen-pass open(s) in model.json for $openMin+ min without a prediction" }
    }
    $st = $null
    $stt = Read-Shared (Join-Path $state 'state.json')
    if ($stt) { try { $st = $stt | ConvertFrom-Json } catch {} }
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
    $line = "{0} {1} pid={2} alive={3} beat_min={4} predicted={5} closes={6} missed={7} void={8} scored={9} opens_{13}min={10} opens_without_prediction={11} predictions_bytes={14} predictions_idle_min={12}" -f `
        (Stamp), $(if ($alerts) { 'ALERT' } else { 'OK' }), $pid0, $alive, $beatAge, $c.predicted, $c.closes, $c.missed, $c.void, $c.scored, $opens, $noPred, $prAge, $openMin, $pr
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
