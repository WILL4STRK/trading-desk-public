# Registers the L1 learner (docs/MEME.md section 18) as two scheduled tasks. Run from an ADMINISTRATOR PowerShell (an S4U
# "run whether logged on or not" task needs it). The tasks run as WILLPC\Willr, RunLevel Limited, no stored password.
# Safe to re-run: -Force replaces a task with the same name. Never starts or stops a task.
#
# HOLD: do not register before desk/skills.py write_json retries PermissionError (its own PR, after the D1 window ends
# 2026-11-30 07:24 UTC). Until then the learner's 30 s read of model.json can make a desk save fail (os.replace onto an
# open file is WinError 5 on Windows, even with FILE_SHARE_DELETE).
#
# First, once, after the PR is merged (pin the clone to the merge commit; never the live folder):
#   git clone https://github.com/WILL4STRK/trading-desk-public.git C:\Trading\learner\repo
#   git -C C:\Trading\learner\repo checkout --detach <merge-sha>
# Then:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Trading\learner\repo\research\learner\register_learner.ps1
$ErrorActionPreference = 'Stop'
$user = 'WILLPC\Willr'
$file = 'C:\Trading\learner\repo\research\learner\run_learner.ps1'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited

function Reg($name, $arguments, $triggers, $settings) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $file $arguments"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    $t = Get-ScheduledTask -TaskName $name
    "{0}: {1}, user={2}, logon={3}, run={4}, multiple={5}, triggers={6}" -f $name, $t.State, $t.Principal.UserId, $t.Principal.LogonType,
        $t.Principal.RunLevel, $t.Settings.MultipleInstances, (($t.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task', '' }) -join '+')
}

# 1) L1 learner: boot (1 min delay) + every 5 minutes, indefinitely. IgnoreNew: a repeat never starts a second runner.
$boot = New-ScheduledTaskTrigger -AtStartup; $boot.Delay = 'PT1M'
$rep  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Reg 'L1 learner' '' @($boot, $rep) `
    (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable)

# 2) L1 learner watchdog: one check every 5 minutes (the runner never ends, so its own repeat cannot run the check).
$boot2 = New-ScheduledTaskTrigger -AtStartup; $boot2.Delay = 'PT6M'
$rep2  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) -RepetitionInterval (New-TimeSpan -Minutes 5)
Reg 'L1 learner watchdog' '-Watch' @($boot2, $rep2) `
    (New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -StartWhenAvailable)
