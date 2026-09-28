# Cue setup: Python venv + dependencies + models + daily work-log task + desktop shortcut.
# Safe to re-run. Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
# Kept outside OneDrive-style synced folders (thousands of files) and outside AppData
# (sandboxed apps get redirected there).
$venv = Join-Path $env:USERPROFILE ".cue\venv"
$py = "$venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating venv at $venv"
    py -3.11 -m venv $venv
}
& $py -m pip install --upgrade pip -q
& $py -m pip install -q -r "$root\requirements.txt"

# Your private copies of the config/profile templates (never committed)
& $py -c "import sys; sys.path.insert(0, r'$root'); from cue.config import ensure_user_files; ensure_user_files()"

# Voice-fingerprint model (who's talking / is it you) and the local fallback model
$model = Join-Path $root "models\wespeaker_en_voxceleb_resnet34_LM.onnx"
if (-not (Test-Path $model)) {
    New-Item -ItemType Directory -Force (Split-Path $model) | Out-Null
    Invoke-WebRequest -Uri "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx" -OutFile $model
}
if (Get-Command ollama -ErrorAction SilentlyContinue) { ollama pull qwen2.5:7b }
else { Write-Host "Ollama not found - install it from https://ollama.com for the offline fallback." }

# Daily at 07:30 (catches up at next logon if the PC was off). Incremental: only new days are summarized.
$action = New-ScheduledTaskAction -Execute "$venv\Scripts\pythonw.exe" -Argument "-m worklog" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "Cue Worklog" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Cue: summarizes your Claude Code/Cowork sessions into your work brief" -Force | Out-Null
Write-Host "Scheduled task 'Cue Worklog' registered (daily 07:30)."

# Desktop shortcut (minimized console so the live transcript is there if you want it)
$desk = [Environment]::GetFolderPath("Desktop")
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\Cue.lnk")
$lnk.TargetPath = "$root\run.cmd"
$lnk.WorkingDirectory = $root
$lnk.WindowStyle = 7
$lnk.IconLocation = "$root\assets\cue.ico"
$lnk.Description = "Cue - real-time call copilot"
$lnk.Save()
Write-Host "Desktop shortcut created: Cue"

if (-not ((& claude auth status 2>$null) -match '"loggedIn": true')) {
    Write-Host "`nClaude CLI is not logged in. Run 'claude auth login', then '.\worklog.cmd --backfill'."
}
