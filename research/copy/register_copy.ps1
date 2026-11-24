# Registers the C1 copy follower (docs/MEME.md section 19) as two scheduled tasks. Run from an ADMINISTRATOR PowerShell (an
# S4U "run whether logged on or not" task needs it). The tasks run as WILLPC\Willr, RunLevel Limited, no stored password.
# Safe to re-run: -Force replaces a task with the same name. Never starts or stops a task.
#
# First, once (pin the clone to the commit that holds the frozen lists; never the live folder):
#   git clone https://github.com/WILL4STRK/trading-desk-public.git C:\Trading\copy\repo
#   git -C C:\Trading\copy\repo checkout --detach <sha>
# Then:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Trading\copy\repo\research\copy\register_copy.ps1
$ErrorActionPreference = 'Stop'
$user = 'WILLPC\Willr'
$file = 'C:\Trading\copy\repo\research\copy\run_copy.ps1'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited

function Reg($name, $arguments, $triggers, $settings) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $file $arguments"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    $t = Get-ScheduledTask -TaskName $name
    "{0}: {1}, user={2}, logon={3}, run={4}, multiple={5}, triggers={6}" -f $name, $t.State, $t.Principal.UserId, $t.Principal.LogonType,
        $t.Principal.RunLevel, $t.Settings.MultipleInstances, (($t.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task', '' }) -join '+')
}

# 1) C1 copy: boot (1 min delay) + every 5 minutes, indefinitely. IgnoreNew: a repeat never starts a second runner.
$boot = New-ScheduledTaskTrigger -AtStartup; $boot.Delay = 'PT1M'
$rep  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Reg 'C1 copy' '' @($boot, $rep) `
    (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable)

# 2) C1 copy watchdog: one check every 5 minutes (the runner never ends, so its own repeat cannot run the check).
$boot2 = New-ScheduledTaskTrigger -AtStartup; $boot2.Delay = 'PT6M'
$rep2  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) -RepetitionInterval (New-TimeSpan -Minutes 5)
Reg 'C1 copy watchdog' '-Watch' @($boot2, $rep2) `
    (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -StartWhenAvailable)
