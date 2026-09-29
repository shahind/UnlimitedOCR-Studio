"""
One-step setup for Unlimited-OCR Studio.

    python install.py                  # create .venv, install CUDA PyTorch + requirements
    python install.py --download-model # ...and pre-download the model (~6.7 GB)
    python install.py --no-venv        # install into the current Python instead

Works on Windows and Linux. Needs Python 3.11-3.13 and an NVIDIA GPU driver (CUDA 12.8+).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
TORCH = ["torch==2.10.0", "torchvision==0.25.0"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"


def run(cmd: list[str]):
    print(">", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-venv", action="store_true", help="install into the running interpreter")
    ap.add_argument("--download-model", action="store_true", help="pre-download baidu/Unlimited-OCR")
    a = ap.parse_args()

    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        sys.exit(f"Python 3.11-3.13 required (this is {sys.version.split()[0]}).")
    if not shutil.which("nvidia-smi"):
        print("WARNING: nvidia-smi not found. Unlimited-OCR only runs on NVIDIA GPUs; "
              "installation continues but conversion will fail without one.\n")

    if a.no_venv:
        py = Path(sys.executable)
    else:
        if not venv_python().exists():
            print(f"Creating virtual environment in {VENV}")
            venv.create(VENV, with_pip=True)
        py = venv_python()

    run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", *TORCH, "--index-url", TORCH_INDEX])
    run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    run([str(py), "-c", "import torch; print('PyTorch', torch.__version__, '| CUDA available:', "
                        "torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) "
                        "if torch.cuda.is_available() else 'no GPU')"])
    if a.download_model:
        run([str(py), "-c", "from huggingface_hub import snapshot_download as s; "
                            "print(s('baidu/Unlimited-OCR', allow_patterns=['*.json','*.py','*.safetensors','*.txt']))"])

    launch = r".venv\Scripts\python app.py" if os.name == "nt" else ".venv/bin/python app.py"
    print(f"\nDone. Start the GUI with:  {'run_gui.bat  (or ' + launch + ')' if os.name == 'nt' else launch}")


if __name__ == "__main__":
    main()
