@echo off
rem Setup: finds an installed Python (preferring one with CUDA PyTorch), installs only missing packages,
rem and downloads the OCR model. Shows sizes and free space first. Safe to re-run.
cd /d "%~dp0"
title Unlimited-OCR Studio setup
set "INSTALL=install.py"
if exist "app\install.py" set "INSTALL=app\install.py"

py -3 --version >nul 2>nul
if not errorlevel 1 (
    py -3 "%INSTALL%" %*
    goto done
)
python --version >nul 2>nul
if not errorlevel 1 (
    python "%INSTALL%" %*
    goto done
)
echo No Python installation was found.
echo Install Python 3.10-3.13 from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH"), then run this setup again.
:done
echo.
pause
