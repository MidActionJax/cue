@echo off
rem Quick work note for things you did outside Claude:   note "restarted the nightly cron"
cd /d "%~dp0"
"%USERPROFILE%\.cue\venv\Scripts\python.exe" -m cue.journal %*
