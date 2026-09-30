"""
Build the Windows release:  python build_windows.py [--version v1.0.0]

Produces release/UnlimitedOCR-Studio-<version>-win64.zip (well under 1 MB):

    UnlimitedOCR-Studio.exe   launcher with icon: runs Setup on first use, then the GUI
    Setup.bat                 reuses an installed Python/PyTorch, installs only missing packages, downloads the model
    app/                      application sources

Nothing heavy is bundled: an existing CUDA PyTorch is reused, and the model is downloaded by Setup.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "UnlimitedOCR-Studio"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
APP_FILES = ["app.py", "ocr_engine.py", "md_render.py", "install.py", "requirements.txt", "assets/icon.ico",
             "assets/icon.png"]

README = """Unlimited-OCR Studio {version} - Windows
===========================================

Converts PDFs and page images to Markdown / text / HTML with Baidu's Unlimited-OCR model,
keeping equations (LaTeX), headings, tables and figures.

Requirements
  - Windows 10/11 64-bit with Python 3.10-3.13 installed (https://www.python.org/downloads/)
  - NVIDIA GPU with at least 8 GB VRAM and a recent driver (CUDA 12.8 or newer)

Getting started
  1. Extract this zip to a folder of your choice.
  2. Run UnlimitedOCR-Studio.exe. The first time it offers to run Setup (Setup.bat), which
       - finds the Python installations on this PC and prefers one that already has CUDA PyTorch,
       - installs only what is missing into it (CUDA PyTorch ~2.9 GB only if absent or CPU-only),
       - downloads the OCR model baidu/Unlimited-OCR (~6.3 GB) to %USERPROFILE%\\.cache\\huggingface
         (set the HF_HOME environment variable first to keep it on another drive).
     It lists everything with sizes and your free space and asks before downloading.
  3. Add PDFs, press Start, preview, and use "Save as" for .md / .txt / .html.

To use a specific Python (e.g. a conda env):  path\\to\\python.exe app\\install.py
Command line:  python app\\ocr_engine.py paper.pdf -o out

Source and documentation: https://github.com/shahind/UnlimitedOCR-Studio
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1.0.0")
    a = ap.parse_args()

    out = ROOT / "release"
    shutil.rmtree(out, ignore_errors=True)
    bundle = out / NAME
    for f in APP_FILES:
        dst = bundle / "app" / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    shutil.copy2(ROOT / "setup.bat", bundle / "Setup.bat")
    (bundle / "README.txt").write_text(README.format(version=a.version), encoding="utf-8", newline="\r\n")
    subprocess.check_call([str(CSC), "/nologo", "/target:winexe", "/optimize+",
                           "/reference:System.Windows.Forms.dll", f"/win32icon:{ROOT / 'assets' / 'icon.ico'}",
                           f"/out:{bundle / (NAME + '.exe')}", str(ROOT / "packaging" / "Launcher.cs")])

    zip_path = out / f"{NAME}-{a.version}-win64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(bundle.rglob("*")):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(bundle))
    print(f"Built {zip_path} ({zip_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
