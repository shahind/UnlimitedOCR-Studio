@echo off
rem Launch Unlimited-OCR Studio with the Python chosen by setup. Files dropped onto this .bat are queued.
cd /d "%~dp0"
if not exist python_path.txt call setup.bat
if not exist python_path.txt exit /b 1
set /p PY=<python_path.txt
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
start "" "%PYW%" app.py %*
