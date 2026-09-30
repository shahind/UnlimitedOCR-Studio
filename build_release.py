"""
Build the release packages:  python build_release.py [--version v1.0.0] [--target windows|linux|all]

  release/UnlimitedOCR-Studio-<version>-win64.zip
      UnlimitedOCR-Studio.exe   launcher with icon (C#, compiled with the compiler built into Windows)
      Setup.bat, README.txt, app/
  release/UnlimitedOCR-Studio-<version>-linux-x86_64.tar.gz
      UnlimitedOCR-Studio       launcher script (runs setup on first use)
      setup.sh, README.txt, app/

Both are tiny: an existing Python/CUDA PyTorch is reused and the model is downloaded by setup.
The Windows launcher can only be compiled on Windows; the Linux package can be built anywhere.
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "UnlimitedOCR-Studio"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
APP_FILES = ["app.py", "ocr_engine.py", "md_render.py", "install.py", "requirements.txt", "assets/icon.ico",
             "assets/icon.png"]
REPO = "https://github.com/shahind/UnlimitedOCR-Studio"

README_WIN = """Unlimited-OCR Studio {version} - Windows
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

Source and documentation: {repo}
"""

README_LINUX = """Unlimited-OCR Studio {version} - Linux
=========================================

Converts PDFs and page images to Markdown / text / HTML with Baidu's Unlimited-OCR model,
keeping equations (LaTeX), headings, tables and figures.

Requirements
  - x86_64 Linux with Python 3.10-3.13 and a desktop session (X11 or Wayland)
  - NVIDIA GPU with at least 8 GB VRAM and the NVIDIA driver (CUDA 12.8 or newer)

Getting started
  1. tar -xzf UnlimitedOCR-Studio-{version}-linux-x86_64.tar.gz
  2. ./UnlimitedOCR-Studio/UnlimitedOCR-Studio
     The first time it runs setup (./setup.sh), which
       - finds the Pythons on this machine (PATH and conda envs) and prefers one that already has
         CUDA PyTorch,
       - installs only what is missing (CUDA PyTorch ~3.6 GB only if absent or CPU-only),
       - on distributions whose system Python blocks pip (Debian 12+, Ubuntu 23.04+) it creates a small
         environment in ~/.local/share/UnlimitedOCR-Studio/env that shares the system packages,
       - downloads the OCR model baidu/Unlimited-OCR (~6.3 GB) to ~/.cache/huggingface
         (set HF_HOME first to keep it elsewhere),
       - tells you if system libraries needed by Qt are missing (apt command included),
       - can add the app to your applications menu.
     It lists everything with sizes and your free space and asks before downloading.
  3. Add PDFs, press Start, preview, and use "Save as" for .md / .txt / .html.

To use a specific Python (e.g. a conda env):  /path/to/env/bin/python app/install.py
Command line:  python3 app/ocr_engine.py paper.pdf -o out

Source and documentation: {repo}
"""


def copy_app(bundle: Path):
    for f in APP_FILES:
        dst = bundle / "app" / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)


def build_windows(version: str, out: Path):
    bundle = out / "windows" / NAME
    copy_app(bundle)
    shutil.copy2(ROOT / "setup.bat", bundle / "Setup.bat")
    (bundle / "README.txt").write_text(README_WIN.format(version=version, repo=REPO), encoding="utf-8",
                                       newline="\r\n")
    subprocess.check_call([str(CSC), "/nologo", "/target:winexe", "/optimize+",
                           "/reference:System.Windows.Forms.dll", f"/win32icon:{ROOT / 'assets' / 'icon.ico'}",
                           f"/out:{bundle / (NAME + '.exe')}", str(ROOT / "packaging" / "Launcher.cs")])
    path = out / f"{NAME}-{version}-win64.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(bundle.rglob("*")):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(bundle))
    return path


def build_linux(version: str, out: Path):
    """Assembled in memory so Unix permissions and LF line endings are right even when built on Windows."""
    files: list[tuple[str, bytes, int]] = []
    for f in APP_FILES:
        data = (ROOT / f).read_bytes()
        if f.endswith((".py", ".txt")):
            data = data.replace(b"\r\n", b"\n")
        files.append((f"app/{f}", data, 0o644))
    lf = lambda p: (ROOT / p).read_bytes().replace(b"\r\n", b"\n")  # noqa: E731
    files += [(NAME, lf("run_gui.sh"), 0o755), ("setup.sh", lf("setup.sh"), 0o755),
              ("README.txt", README_LINUX.format(version=version, repo=REPO).encode(), 0o644)]
    path = out / f"{NAME}-{version}-linux-x86_64.tar.gz"
    now = int(time.time())
    with tarfile.open(path, "w:gz", compresslevel=9) as tar:
        dirs = sorted({n.rsplit("/", 1)[0] for n, *_ in files if "/" in n})
        for d in [""] + dirs:
            ti = tarfile.TarInfo(f"{NAME}/{d}".rstrip("/"))
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, now
            tar.addfile(ti)
        for name, data, mode in files:
            ti = tarfile.TarInfo(f"{NAME}/{name}")
            ti.size, ti.mode, ti.mtime = len(data), mode, now
            tar.addfile(ti, io.BytesIO(data))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1.0.0")
    ap.add_argument("--target", choices=["windows", "linux", "all"], default="all")
    a = ap.parse_args()
    out = ROOT / "release"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    built = []
    if a.target in ("windows", "all"):
        built.append(build_windows(a.version, out))
    if a.target in ("linux", "all"):
        built.append(build_linux(a.version, out))
    for p in built:
        print(f"Built {p} ({p.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
