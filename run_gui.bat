@echo off
rem Launch Unlimited-OCR Studio. Files dropped onto this .bat are added to the queue.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" app.py %*
