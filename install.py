"""
Setup for Unlimited-OCR Studio (Windows and Linux). Reuses an existing Python + PyTorch installation and installs
only what is missing.

    python install.py               # pick a Python (prefers one that already has CUDA PyTorch), install missing parts
    python install.py --python /path/to/python   # use this interpreter (e.g. a conda env)
    python install.py --dry-run     # only show what would be installed
    python install.py --skip-model  # don't download the model now (it downloads on first use)
    python install.py -y            # no questions

Nothing is downloaded before the plan (packages, sizes, free space) is shown and confirmed.
The chosen interpreter is saved to python_path.txt and used by the launchers.
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
WINDOWS = os.name == "nt"
GB = 2**30
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_PKGS = ["torch==2.10.0", "torchvision==0.25.0"]
TORCH_PY = ((3, 10), (3, 13))            # Python versions with CUDA wheels for TORCH_PKGS
MIN_TORCH = (2, 4)
# rough download sizes for the summary (on Linux the CUDA libraries come as separate nvidia-* wheels)
SIZES = {"torch": (2.9 if WINDOWS else 3.6) * GB, "pyside6": 0.25 * GB, "matplotlib": 0.01 * GB,
         "pymupdf": 0.02 * GB}
DEFAULT_SIZE = 0.01 * GB
MODEL_SIZE = 6.3 * GB
# Linux: environment that shares the system's packages, used only when the system Python refuses pip (PEP 668)
SHARED_ENV = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "UnlimitedOCR-Studio" / "env"
# Linux: system libraries the Qt wheels need that minimal installs often lack (soname -> Debian/Ubuntu package)
QT_LIBS = {"libxcb-cursor.so.0": "libxcb-cursor0", "libxkbcommon-x11.so.0": "libxkbcommon-x11-0",
           "libEGL.so.1": "libegl1", "libnss3.so": "libnss3", "libxkbfile.so.1": "libxkbfile1"}

PROBE = r"""
import json, os, sys, sysconfig, importlib.metadata as md
reqs = json.loads(sys.argv[1])
r = {"exe": sys.executable, "ver": list(sys.version_info[:3]), "missing": [], "installed": {},
     "prefix": sys.prefix, "platform": sysconfig.get_platform(),
     "target": sysconfig.get_path("purelib"),
     "venv": sys.prefix != sys.base_prefix,
     "managed": os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))}
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
if not os.access(r["target"], os.W_OK) and not r["venv"]:   # pip falls back to a per-user install
    import site
    r["target"] = site.getusersitepackages()
print("PROBE" + json.dumps(r))
"""


def requirements() -> list[str]:
    lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()]
    return [ln for ln in lines if ln]


def probe(exe: str) -> dict | None:
    try:
        out = subprocess.run([exe, "-c", PROBE, json.dumps(requirements())], capture_output=True, text=True,
                             timeout=180).stdout
        m = re.search(r"PROBE(\{.*\})", out)
        return json.loads(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def conda_pythons() -> list[str]:
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    if not conda:
        return []
    try:
        envs = json.loads(subprocess.run([conda, "env", "list", "--json"], capture_output=True, text=True,
                                         timeout=60).stdout)["envs"]
    except Exception:  # noqa: BLE001
        return []
    return [str(Path(e) / ("python.exe" if WINDOWS else "bin/python")) for e in envs]


def candidates() -> list[str]:
    """The running interpreter, Pythons from the 'py' launcher (Windows) or PATH, conda envs, and a previous choice."""
    found = [sys.executable]
    if PY_FILE.exists():
        found.append(PY_FILE.read_text(encoding="utf-8").strip())
    if SHARED_ENV.exists():
        found.append(str(SHARED_ENV / "bin" / "python"))
    if WINDOWS and shutil.which("py"):
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True).stdout
        found += re.findall(r"([A-Za-z]:\\\S.*?python\.exe)\s*$", out, re.M)
    names = ["python", "python3"] + [f"python3.{m}" for m in range(10, 14)]
    for name in names:
        p = shutil.which(name)
        if p and "WindowsApps" not in p:          # skip the Microsoft Store stub
            found.append(p)
    found += conda_pythons()
    seen, result = set(), []
    for f in found:
        if not f or not Path(f).exists():
            continue
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def torch_ok(info: dict) -> bool:
    return bool(info["torch"] and info["cuda"]
                and tuple(int(x) for x in re.findall(r"\d+", info["torch"])[:2]) >= MIN_TORCH)


def score(info: dict) -> int:
    """Prefer interpreters whose PyTorch can be used as is; otherwise keep discovery order (running Python,
    then PATH, then conda envs), so other applications' environments are not modified by default."""
    if torch_ok(info):
        return 2 if info["gpu"] else 1
    return 0


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
    note = "  [system-managed: pip blocked]" if info["managed"] and not info["venv"] else ""
    return f"Python {v}  {t}{note}\n      {info['exe']}"


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


def missing_qt_libs() -> list[str]:
    if WINDOWS or sys.platform == "darwin":
        return []
    try:
        cache = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
    except OSError:
        cache = subprocess.run(["/sbin/ldconfig", "-p"], capture_output=True, text=True).stdout
    return [pkg for lib, pkg in QT_LIBS.items() if lib not in cache]


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
        run([py, get_pip, "--no-warn-script-location"] + ([] if WINDOWS or os.geteuid() == 0 else ["--user"]))
    finally:
        get_pip.unlink(missing_ok=True)


def make_plan(choice: dict) -> list[tuple[str, list, float]]:
    """(description, pip arguments, approx. download bytes) for everything missing in this interpreter."""
    steps = []
    ver = tuple(choice["ver"][:2])
    tv = choice["torch"] and tuple(int(x) for x in re.findall(r"\d+", choice["torch"])[:2])
    if not choice["torch"] or not choice["cuda"] or tv < MIN_TORCH:
        if not TORCH_PY[0] <= ver <= TORCH_PY[1]:
            sys.exit(f"CUDA PyTorch wheels need Python 3.10-3.13 (selected Python is {'.'.join(map(str, ver))}).")
        why = ("not installed" if not choice["torch"] else
               f"installed {choice['torch']} is CPU-only; replaces it" if not choice["cuda"] else
               f"installed {choice['torch']} is older than {'.'.join(map(str, MIN_TORCH))}; replaces it")
        steps.append((f"CUDA PyTorch 2.10 + torchvision  ({why})", [*TORCH_PKGS, "--index-url", TORCH_INDEX],
                      SIZES["torch"]))
    elif not choice["torchvision"]:
        cu = "cu" + choice["cuda"].replace(".", "")
        steps.append((f"torchvision matching PyTorch {choice['torch']}",
                      [f"torchvision==0.{tv[1] + 15}.*", "--index-url", f"https://download.pytorch.org/whl/{cu}"],
                      0.01 * GB))
    for req in choice["missing"]:
        name = re.split(r"[<>=!~ \[]", req)[0].lower()
        steps.append((req, [req.split("   ")[0]], SIZES.get(name, DEFAULT_SIZE)))
    return steps


def add_desktop_entry(py: str, yes: bool):
    """Linux: add the app to the applications menu (freedesktop .desktop file)."""
    launcher = next((p for p in (ROOT.parent / "UnlimitedOCR-Studio", ROOT / "run_gui.sh") if p.exists()), None)
    if WINDOWS or sys.platform == "darwin" or launcher is None:
        return
    apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
    entry = apps / "unlimited-ocr-studio.desktop"
    if not ask(f"\nAdd Unlimited-OCR Studio to the applications menu ({entry})?", yes):
        return
    apps.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=Unlimited-OCR Studio\n"
        "Comment=Convert PDFs to Markdown with equations (Baidu Unlimited-OCR)\n"
        f'Exec="{launcher}" %F\nIcon={ROOT / "assets" / "icon.png"}\nTerminal=false\n'
        "Categories=Office;Graphics;Utility;\nMimeType=application/pdf;image/png;image/jpeg;\n", encoding="utf-8")
    entry.chmod(0o755)
    print(f"Added {entry}")


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
    exes = [str(Path(a.python).absolute())] if a.python else candidates()
    infos, seen = [], set()
    for info in filter(None, map(probe, exes)):   # python3 and python3.x are often the same interpreter
        key = (os.path.normcase(os.path.realpath(info["exe"])), os.path.normcase(info["prefix"]))
        usable = tuple(info["ver"][:2]) >= (3, 10) and not info["platform"].startswith(("mingw", "cygwin", "msys"))
        if key not in seen and usable:
            seen.add(key)
            infos.append(info)
    if not infos:
        sys.exit("No usable Python found. Install Python 3.10-3.13 (https://www.python.org/downloads/ or your "
                 "package manager) and run setup again.")
    infos.sort(key=score, reverse=True)           # stable: the running interpreter wins ties
    for n, i in enumerate(infos, 1):
        print(f"  [{n}] {describe(i)}")
    choice = infos[0]
    if len(infos) > 1 and not a.yes:
        sel = prompt(f"\nUse which Python? [1-{len(infos)}, Enter = 1] ")
        if sel.isdigit() and 1 <= int(sel) <= len(infos):
            choice = infos[int(sel) - 1]
    py = choice["exe"]
    steps = make_plan(choice)
    want_model = not a.skip_model and not model_downloaded()
    shared_env = bool(steps) and choice["managed"] and not choice["venv"]

    # ---------------------------------------------------------------- summary
    print(f"\nSelected: {py}")
    print("PyTorch:  " + ("will be installed" if any("CUDA PyTorch" in s[0] for s in steps)
                          else f"kept: {choice['torch']}"))
    if steps:
        print("\nWill install (existing packages are kept unless listed):")
        for desc, _, size in steps:
            print(f"  - {desc:<60} ~{size / GB:.2f} GB")
        where = SHARED_ENV if shared_env else Path(choice["target"])
        print(f"  into {where}  (free: {free(where) / GB:.1f} GB)")
        if shared_env:
            print("\n  This Python is managed by the system package manager, which blocks pip installs.\n"
                  f"  Setup will create a small environment in {SHARED_ENV}\n"
                  "  that SHARES the system's packages (--system-site-packages): everything already installed is\n"
                  "  reused and only the packages above are added. (Alternatively, run setup with a conda env:\n"
                  "  --python /path/to/env/bin/python)")
    else:
        print("\nAll libraries are already installed.")
    qt_missing = missing_qt_libs()
    if qt_missing:
        print(f"\n  Note: the Qt GUI needs these system libraries, which were not found:\n"
              f"    sudo apt install {' '.join(qt_missing)}      (Debian/Ubuntu package names)")
    if want_model:
        print(f"\nOCR model baidu/Unlimited-OCR: ~{MODEL_SIZE / GB:.1f} GB -> {hf_cache()} "
              f"(free: {free(hf_cache()) / GB:.1f} GB)\n  (set HF_HOME to store it on another drive)")
    elif not a.skip_model:
        print("\nOCR model: already downloaded")
    total = sum(s[2] for s in steps) + (MODEL_SIZE if want_model else 0)
    print(f"\nTotal download: ~{total / GB:.1f} GB")
    if a.dry_run:
        return
    if (steps or want_model) and not ask("Continue?", a.yes):
        sys.exit("Setup cancelled.")

    # ---------------------------------------------------------------- install
    if shared_env:
        env_py = SHARED_ENV / "bin" / "python"
        if not env_py.exists():
            try:
                run([py, "-m", "venv", "--system-site-packages", SHARED_ENV])
            except subprocess.CalledProcessError:
                shutil.rmtree(SHARED_ENV, ignore_errors=True)
                v = ".".join(map(str, choice["ver"][:2]))
                sys.exit(f"Could not create the environment. Install venv support (e.g. sudo apt install "
                         f"python{v}-venv) and run setup again.")
        py = str(env_py)
    if steps:
        ensure_pip(py)
        for desc, args, _ in steps:
            print(f"\n== {desc}")
            run([py, "-m", "pip", "install", "--no-warn-script-location", *args])
    PY_FILE.write_text(py, encoding="utf-8")
    run([py, "-c", "import torch; print('PyTorch', torch.__version__, '| CUDA available:', torch.cuda.is_available())"])
    if want_model:
        run([py, ROOT / "ocr_engine.py", "--download-model", "--yes"])
    add_desktop_entry(py, a.yes)
    start = "UnlimitedOCR-Studio.exe or run_gui.bat" if WINDOWS else "./UnlimitedOCR-Studio (or ./run_gui.sh)"
    print(f"\nSetup complete. Python used: {py}\nStart the app with {start}.")
    if qt_missing:
        print(f"Remember to install the missing system libraries first: sudo apt install {' '.join(qt_missing)}")


if __name__ == "__main__":
    main()
