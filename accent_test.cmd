@echo off
rem Compare Whisper models on a recording of a hard-to-understand speaker.
rem   accent_test.cmd "C:\path\to\meeting.mp4" --start 300 --length 60
rem   accent_test.cmd --record 60        (captures 60 s of what you hear, then tests it)
cd /d "%~dp0"
"%USERPROFILE%\.cue\venv\Scripts\python.exe" -m cue.accent_test %*
pause
