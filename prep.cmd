@echo off
rem Builds profiles\interview\prep.md from your resume / job description + work history.
rem Put the job description in profiles\interview\job.md first (and your resume as a PDF/DOCX).
cd /d "%~dp0"
"%USERPROFILE%\.cue\venv\Scripts\python.exe" -m worklog.interview_prep %*
pause
