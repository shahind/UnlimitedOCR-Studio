"""
Setup for Unlimited-OCR Studio. Reuses an existing Python + PyTorch installation and installs only what is missing.

    python install.py               # pick a Python (prefers one that already has CUDA PyTorch), install missing parts
    python install.py --python C:\\path\\to\\python.exe   # use this interpreter (e.g. a conda env)
    python install.py --dry-run     # only show what would be installed
    python install.py --skip-model  # don't download the model now (it downloads on first use)
    python install.py -y            # no questions

Nothing is downloaded before the plan (packages, sizes, free space) is shown and confirmed.
The chosen interpreter is saved to python_path.txt and used by run_gui.bat / UnlimitedOCR-Studio.exe.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY_FILE = ROOT / "python_path.txt"
GB = 2**30
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_PKGS = ["torch==2.10.0", "torchvision==0.25.0"]
TORCH_PY = ((3, 10), (3, 13))            # Python versions with CUDA wheels for TORCH_PKGS
MIN_TORCH = (2, 4)
# rough download sizes for the summary
SIZES = {"torch": 2.9 * GB, "pyside6": 0.25 * GB, "matplotlib": 0.01 * GB, "pymupdf": 0.02 * GB}
DEFAULT_SIZE = 0.01 * GB
MODEL_SIZE = 6.3 * GB

PROBE = r"""
import json, sys, importlib.metadata as md
reqs = json.loads(sys.argv[1])
r = {"exe": sys.executable, "ver": list(sys.version_info[:3]), "missing": [], "installed": {}}
try:
    from packaging.requirements import Requirement
except Exception:
    Requirement = None
for line in reqs:
    name = line.split(";")[0]
    for ch in "<>=!~ [":
        name = name.split(ch)[0]
    try:
        v = md.version(name)
        r["installed"][name] = v
        if Requirement and v not in Requirement(line).specifier:
            r["missing"].append(line + "   (installed: " + v + ")")
    except md.PackageNotFoundError:
        r["missing"].append(line)
try:
    import torch
    r["torch"] = torch.__version__
    r["cuda"] = torch.version.cuda
    r["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
except Exception:
    r["torch"] = r["cuda"] = r["gpu"] = None
try:
    r["torchvision"] = md.version("torchvision")
except md.PackageNotFoundError:
    r["torchvision"] = None
print("PROBE" + json.dumps(r))
"""


def requirements() -> list[str]:
    lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()]
    return [ln for ln in lines if ln]


def probe(exe: str) -> dict | None:
    try:
        out = subprocess.run([exe, "-c", PROBE, json.dumps(requirements())], capture_output=True, text=True,
                             timeout=120).stdout
        m = re.search(r"PROBE(\{.*\})", out)
        return json.loads(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def candidates() -> list[str]:
    """The running interpreter plus Pythons registered with the Windows 'py' launcher or on PATH."""
    found = [sys.executable]
    if shutil.which("py"):
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True).stdout
        found += re.findall(r"([A-Za-z]:\\\S.*?python\.exe)\s*$", out, re.M)
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:          # skip the Microsoft Store stub
            found.append(p)
    seen, result = set(), []
    for f in found:
        key = os.path.normcase(os.path.realpath(f))
        if key not in seen and Path(f).exists():
            seen.add(key)
            result.append(f)
    return result


def score(info: dict) -> int:
    if info["gpu"]:
        return 3                                   # CUDA PyTorch that sees the GPU
    if info["cuda"]:
        return 2                                   # CUDA build, GPU not visible (driver?)
    return 1 if info["torch"] else 0


def describe(info: dict) -> str:
    v = ".".join(map(str, info["ver"]))
    if info["gpu"]:
        t = f"PyTorch {info['torch']} (CUDA, {info['gpu']})"
    elif info["cuda"]:
        t = f"PyTorch {info['torch']} (CUDA build, no GPU visible)"
    elif info["torch"]:
        t = f"PyTorch {info['torch']} (CPU only)"
    else:
        t = "no PyTorch"
    return f"Python {v}  {t}\n      {info['exe']}"


def free(path: Path) -> float:
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def hf_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def model_downloaded() -> bool:
    snaps = hf_cache() / "models--baidu--Unlimited-OCR" / "snapshots"
    return any(snaps.glob("*/*.safetensors")) if snaps.exists() else False


def prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:                               # no console input: take the default
        print()
        return ""


def ask(question: str, yes: bool) -> bool:
    return yes or prompt(f"{question} [Y/n] ").lower() in ("", "y", "yes")


def run(cmd: list):
    print(">", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call([str(c) for c in cmd])


def ensure_pip(py: str):
    if subprocess.run([py, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        return
    print("Installing pip...")
    get_pip = ROOT / "get-pip.py"
    urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
    try:
        run([py, get_pip, "--no-warn-script-location"])
    finally:
        get_pip.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", help="interpreter to install into (default: auto-detect)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan without installing anything")
    ap.add_argument("--skip-model", action="store_true", help="don't download the model now")
    ap.add_argument("-y", "--yes", action="store_true", help="don't ask questions")
    a = ap.parse_args()

    print("Unlimited-OCR Studio setup\n\nLooking for Python installations...")
    if a.python and not Path(a.python).exists():
        sys.exit(f"Python not found: {a.python}")
    exes = [str(Path(a.python).resolve())] if a.python else candidates()
    infos = [i for i in (probe(c) for c in exes) if i]
    if not infos:
        sys.exit("No usable Python found. Install Python 3.10-3.13 from https://www.python.org/downloads/ "
                 "and run setup again.")
    infos.sort(key=score, reverse=True)           # stable: the running interpreter wins ties
    for n, i in enumerate(infos, 1):
        print(f"  [{n}] {describe(i)}")
    choice = infos[0]
    if len(infos) > 1 and not a.yes:
        sel = prompt(f"\nUse which Python? [1-{len(infos)}, Enter = 1] ")
        if sel.isdigit() and 1 <= int(sel) <= len(infos):
            choice = infos[int(sel) - 1]
    py = choice["exe"]
    ver = tuple(choice["ver"][:2])

    # ---------------------------------------------------------------- plan
    steps: list[tuple[str, list, float]] = []     # (description, pip args, download bytes)
    tv = choice["torch"] and tuple(int(x) for x in re.findall(r"\d+", choice["torch"])[:2])
    if not choice["torch"] or not choice["cuda"] or tv < MIN_TORCH:
        if not TORCH_PY[0] <= ver <= TORCH_PY[1]:
            sys.exit(f"CUDA PyTorch wheels need Python 3.10-3.13 (selected Python is {'.'.join(map(str, ver))}).")
        why = ("not installed" if not choice["torch"] else
               f"installed {choice['torch']} is CPU-only" if not choice["cuda"] else
               f"installed {choice['torch']} is older than {'.'.join(map(str, MIN_TORCH))}")
        steps.append((f"CUDA PyTorch 2.10 + torchvision  ({why}; replaces it)" if choice["torch"] else
                      f"CUDA PyTorch 2.10 + torchvision  ({why})",
                      [*TORCH_PKGS, "--index-url", TORCH_INDEX], SIZES["torch"]))
    elif not choice["torchvision"]:
        cu = "cu" + choice["cuda"].replace(".", "")
        steps.append((f"torchvision matching PyTorch {choice['torch']}",
                      [f"torchvision==0.{tv[1] + 15}.*", "--index-url", f"https://download.pytorch.org/whl/{cu}"],
                      0.01 * GB))
    for req in choice["missing"]:
        name = re.split(r"[<>=!~ \[]", req)[0].lower()
        steps.append((req, [req.split("   ")[0]], SIZES.get(name, DEFAULT_SIZE)))
    want_model = not a.skip_model and not model_downloaded()

    print(f"\nSelected: {py}")
    print(f"PyTorch:  {'kept: ' + choice['torch'] if not any('PyTorch 2.10' in s[0] for s in steps) else 'will be installed'}")
    lib_bytes = sum(s[2] for s in steps)
    if steps:
        print("\nWill install (existing packages are kept unless listed):")
        for desc, _, size in steps:
            print(f"  - {desc:<60} ~{size / GB:.2f} GB")
        site = Path(sys.prefix if py == sys.executable else Path(py).parent)
        print(f"  into {site}  (free: {free(site) / GB:.1f} GB)")
    else:
        print("\nAll libraries are already installed.")
    if want_model:
        print(f"\nOCR model baidu/Unlimited-OCR: ~{MODEL_SIZE / GB:.1f} GB -> {hf_cache()} "
              f"(free: {free(hf_cache()) / GB:.1f} GB)\n  (set HF_HOME to store it on another drive)")
    elif not a.skip_model:
        print("\nOCR model: already downloaded")
    total = lib_bytes + (MODEL_SIZE if want_model else 0)
    print(f"\nTotal download: ~{total / GB:.1f} GB")
    if a.dry_run:
        return
    if (steps or want_model) and not ask("Continue?", a.yes):
        sys.exit("Setup cancelled.")

    # ---------------------------------------------------------------- install
    if steps:
        ensure_pip(py)
        for desc, args, _ in steps:
            print(f"\n== {desc}")
            run([py, "-m", "pip", "install", "--no-warn-script-location", *args])
    PY_FILE.write_text(py, encoding="utf-8")
    run([py, "-c", "import torch; print('PyTorch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"])
    if want_model:
        run([py, ROOT / "ocr_engine.py", "--download-model", "--yes"])
    print(f"\nSetup complete. Python used: {py}\nStart the app with UnlimitedOCR-Studio.exe or run_gui.bat.")


if __name__ == "__main__":
    main()
