@echo off
rem Updates profiles\work\ from your Claude Code / Cowork history.
rem   worklog.cmd              incremental (same as the daily scheduled task)
rem   worklog.cmd --backfill   rebuild everything since worklog.since in config.yaml
cd /d "%~dp0"
"%USERPROFILE%\.cue\venv\Scripts\python.exe" -m worklog %*
pause
