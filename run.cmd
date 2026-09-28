@echo off
rem Starts Cue with a console window that shows the live transcript.
rem   run.cmd --mode interview     start in interview mode
rem   run.cmd --demo               no audio; fake question to test the overlay
cd /d "%~dp0"
"%USERPROFILE%\.cue\venv\Scripts\python.exe" -m cue %*
if errorlevel 1 pause
