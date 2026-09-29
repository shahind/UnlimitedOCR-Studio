@echo off
rem One-time setup: creates .venv with CUDA PyTorch + requirements and downloads the model (~6.7 GB).
cd /d "%~dp0"
py -3.13 install.py --download-model 2>nul || py -3 install.py --download-model || python install.py --download-model
pause
