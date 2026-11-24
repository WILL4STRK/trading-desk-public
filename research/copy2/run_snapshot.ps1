# C2 daily snapshot (docs/MEME.md section 20.A). Paper research only: DexScreener reads, no Helius, no GeckoTerminal.
# One run: takes today's snapshot (up to 3 tries, 10 minutes apart), logs one line, and after the 7th snapshot disables its
# own task. Runs ONLY from the pinned clone C:\Trading\copy2\repo (detached), never from the live desk folder.
$ErrorActionPreference = 'Continue'
$repo  = 'C:\Trading\copy2\repo'
$home2 = 'C:\Trading\research\copy2'
$log   = Join-Path $home2 'snapshot.log'
$pidF  = Join-Path $home2 'snapshot.pid'
$task  = 'C2 snapshot'
$days  = @('2026-11-25','2026-11-26','2026-11-27','2026-11-28','2026-11-29','2026-11-30','2026-12-01')
New-Item -ItemType Directory -Path (Join-Path $home2 'snapshots') -Force | Out-Null
function Stamp { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + 'Z' }
function Say($m) { Add-Content -Path $log -Value ("{0} {1}" -f (Stamp), $m) -Encoding UTF8 }

$here = (Resolve-Path $PSScriptRoot).Path
if (-not $here.StartsWith($repo, [StringComparison]::OrdinalIgnoreCase)) { Say "refusing to run from $here; run from $repo"; exit 1 }
& git -C $repo symbolic-ref -q HEAD *> $null
if ($LASTEXITCODE -eq 0) { Say "$repo is on a branch; pin it (git checkout --detach <sha>)"; exit 1 }

$mutex = New-Object System.Threading.Mutex($false, 'Global\C2SnapshotRunner')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    if (Test-Path $pidF) {
        $old = 0; [void][int]::TryParse((Get-Content $pidF -Raw).Trim(), [ref]$old)
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$old" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -like '*snapshot.py*') { Say "snapshot already running as pid $old"; exit 0 }
        if ($old) { Say "stale pid $old (not running)" }
    }
    $today = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
    $file  = Join-Path $home2 ("snapshots\{0}.json" -f $today)
    if ($days -notcontains $today) { Say "$today is not a snapshot day" }
    elseif (Test-Path $file) { Say "$today already taken" }
    else {
        Set-Location $repo
        $env:PYTHONUTF8 = '1'
        $head = (& git -C $repo rev-parse --short HEAD)
        for ($i = 1; $i -le 3 -and -not (Test-Path $file); $i++) {
            $proc = Start-Process -FilePath 'py' -ArgumentList '-3.13','-X','utf8','research\copy2\snapshot.py','take' -NoNewWindow -PassThru `
                -RedirectStandardOutput (Join-Path $home2 'snapshot.out') -RedirectStandardError (Join-Path $home2 'snapshot.err')
            Set-Content -Path $pidF -Value $proc.Id
            $proc.WaitForExit()
            $out = (Get-Content (Join-Path $home2 'snapshot.out') -Raw) + ' ' + (Get-Content (Join-Path $home2 'snapshot.err') -Raw)
            Say ("try {0} at {1}: exit {2} {3}" -f $i, $head, $proc.ExitCode, ($out -replace '\s+', ' ').Trim())
            if (-not (Test-Path $file) -and $i -lt 3) { Start-Sleep -Seconds 600 }
        }
        Remove-Item $pidF -ErrorAction SilentlyContinue
    }
    $have = @($days | Where-Object { Test-Path (Join-Path $home2 ("snapshots\{0}.json" -f $_)) }).Count
    if ($have -ge 7 -or $today -gt $days[-1]) {
        try { Disable-ScheduledTask -TaskName $task -ErrorAction Stop | Out-Null; Say "all $have snapshots done; task disabled" }
        catch { Say "all $have snapshots done; could not disable the task ($($_.Exception.Message)); its trigger ends 2026-12-01 01:00Z anyway" }
    }
} finally { $mutex.ReleaseMutex() }
