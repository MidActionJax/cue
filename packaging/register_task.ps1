# Registers (or removes) the daily "Cue Worklog" task for the installed app.
#   register_task.ps1 -Exe "C:\...\Cue\Cue.exe"
#   register_task.ps1 -Remove
param([string]$Exe, [switch]$Remove)

if ($Remove) {
    Unregister-ScheduledTask -TaskName "Cue Worklog" -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

# Daily at 07:30 (catches up at next logon if the PC was off). Incremental: only new days are summarized.
$action = New-ScheduledTaskAction -Execute $Exe -Argument "worklog" -WorkingDirectory (Split-Path $Exe)
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "Cue Worklog" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Cue: summarizes your Claude Code/Cowork sessions into your work brief" -Force | Out-Null
