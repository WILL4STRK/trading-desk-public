# Registers the C2 daily snapshot task (docs/MEME.md section 20.A). Run from an ADMINISTRATOR PowerShell (an S4U "run whether
# logged on or not" task needs it). Runs as WILLPC\Willr, RunLevel Limited, no stored password. Safe to re-run (-Force).
# Never starts or stops the task. One run a day at 00:05 UTC, 2026-11-25 to 2026-12-01; the trigger expires 2026-12-01 01:00 UTC,
# and run_snapshot.ps1 disables the task after the 7th snapshot.
#
# First, once (pin the clone to a commit; never the live folder):
#   git clone https://github.com/WILL4STRK/trading-desk-public.git C:\Trading\copy2\repo
#   git -C C:\Trading\copy2\repo checkout --detach <sha>
# Then:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Trading\copy2\repo\research\copy2\register_copy2.ps1
$ErrorActionPreference = 'Stop'
$user = 'WILLPC\Willr'
$file = 'C:\Trading\copy2\repo\research\copy2\run_snapshot.ps1'
$name = 'C2 snapshot'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$startUtc = [DateTime]::SpecifyKind([DateTime]'2026-11-25T00:05:00', 'Utc')
$trigger = New-ScheduledTaskTrigger -Daily -At $startUtc.ToLocalTime()
$trigger.EndBoundary = '2026-12-01T01:00:00Z'
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $file"
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$t = Get-ScheduledTask -TaskName $name
"{0}: {1}, user={2}, logon={3}, run={4}, multiple={5}, start={6}, end={7}" -f $name, $t.State, $t.Principal.UserId, $t.Principal.LogonType,
    $t.Principal.RunLevel, $t.Settings.MultipleInstances, $t.Triggers[0].StartBoundary, $t.Triggers[0].EndBoundary
