<img src="assets/icon.png" width="96" align="right" alt="">

# Unlimited-OCR Studio

This tool converts PDFs (and page images) to **Markdown**, **plain text** or **HTML**. It uses Baidu's
[Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR), a 3B vision-language OCR model, and runs locally with no
API limits. The output keeps:

- **Equations** as LaTeX: inline `$…$`, display `$$…$$`, including `\tag{n}` numbers, matrices and `array` blocks.
- **Structure**: headings (from the model's layout labels), paragraphs in reading order (two-column papers work),
  and tables as HTML `<table>` blocks.
- **Figures and charts**, cropped from the page and linked as `![](name_images/p004_fig1.png)`.
- Page headers, footers and page numbers are removed by default (this is optional).

## Requirements

- Windows/Linux with an **NVIDIA GPU with at least 8 GB of VRAM**. The model's code is CUDA-only. It uses about 6.3 GB
  of VRAM and was tested on an RTX 4060 Laptop GPU with 8 GB.
- Python 3.10–3.13. An existing CUDA PyTorch installation is reused.

## Download

Both packages are on [Releases](https://github.com/shahind/UnlimitedOCR-Studio/releases) and are under 100 KB each.
On first use each one runs Setup (described below) and then opens the app.

- **Windows:** extract `UnlimitedOCR-Studio-<version>-win64.zip` and run **`UnlimitedOCR-Studio.exe`**.
- **Linux (x86_64):** extract the tarball and run the launcher:

  ```bash
  tar -xzf UnlimitedOCR-Studio-<version>-linux-x86_64.tar.gz
  ./UnlimitedOCR-Studio/UnlimitedOCR-Studio
  ```

  Setup can also add the app to your applications menu.

## Setup

Setup doesn't create a virtual environment and doesn't download anything you already have:

1. It finds the Python installations (the `py` launcher, PATH and conda environments) and prefers one that
   already has CUDA PyTorch.
2. It lists exactly what is missing, with download sizes and free disk space, and asks before continuing.
3. It installs only the missing packages. CUDA PyTorch (~2.9 GB on Windows, ~3.6 GB on Linux) is installed only if
   PyTorch is absent, CPU-only or older than 2.4.
4. It downloads the OCR model `baidu/Unlimited-OCR` (~6.3 GB) to the Hugging Face cache, unless it is already there.
   Set `HF_HOME` to keep the model on another drive.
5. On Linux, some distributions block pip for the system Python (Debian 12+, Ubuntu 23.04+). There, setup creates a
   small environment in `~/.local/share/UnlimitedOCR-Studio/env` that shares the system's packages, so nothing is
   downloaded twice. It also prints the `apt install` command if system libraries needed by Qt are missing.

```bash
python install.py                 # interactive; or double-click setup.bat (Windows) / run ./setup.sh (Linux)
python install.py --dry-run       # only show the plan
python install.py --python C:\path\to\python.exe   # use a specific interpreter / conda env
python install.py --skip-model -y
```

The chosen interpreter is saved in `python_path.txt`, which the launchers use.

To install manually instead, run the following. Install CUDA PyTorch first only if you don't have it.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
python ocr_engine.py --download-model
```

## GUI

Run **`run_gui.bat`** (Windows) or **`./run_gui.sh`** (Linux), or run `python app.py [files…]`.

1. **Add files**, or drag and drop PDFs, images or folders onto the window.
   To convert only some pages, edit the **Range** column (e.g. `1-3,7,10-`).
2. Choose settings and press **Start** (F5). The model loads once on the first run, which takes about 10 s.
3. Watch the progress:
   - The **Source** panel shows the page being read.
   - **Live output** streams the model's raw output.
   - The status bar shows pages done, speed and ETA.
4. **Preview** shows the rendered Markdown with MathJax-typeset equations.
   **Markdown** is editable, so you can fix mistakes before saving.
5. **Save as…** (Ctrl+S) writes `.md`, `.txt` or `.html`. **Save all…** saves every finished file.
   You can also turn on *Auto-save each finished file* for batch jobs.

| Setting | Meaning |
|---|---|
| Mode `gundam` | Tiles each page, which gives the best accuracy. This is the default and is recommended. |
| Mode `base` | Uses one 1024 px view per page. It is a bit faster. |
| Mode `multi` | Reads several pages per pass, so the model sees context across pages. It needs more VRAM. |
| Render DPI | The PDF rasterisation DPI. Baidu recommends 300. |
| Max tokens / page | Stops runaway generation. Dense pages use about 3k tokens. |
| Use `$`/`$$` | Converts `\( \)` and `\[ \]` to dollar delimiters, which Obsidian, Typora, GitHub and Pandoc understand. |

The preview and exported HTML load MathJax from a CDN. When you are offline, the equations show as raw LaTeX, but the
saved `.md` is unaffected.

## Command line

```bash
python ocr_engine.py paper.pdf                       # -> paper.md + paper_images/
python ocr_engine.py a.pdf b.pdf -o out --format html
python ocr_engine.py scan.pdf --pages 1-5 --mode base -q
```

Run `python ocr_engine.py -h` for all options (`--keep-tex-delims`, `--keep-headers`, `--no-figures`, …).

## Performance

On an RTX 4060 Laptop GPU, generation runs at about 30 tokens/s. A dense two-column journal page takes about 90 s,
and a simple page takes 10–20 s. The GUI keeps the model loaded between files.

## Files

- `ocr_engine.py`: model download, model loading, PDF rendering, OCR, Markdown post-processing, saving, and the CLI.
- `md_render.py`: Markdown to HTML conversion with protected LaTeX (used by the preview and the HTML export).
- `app.py`: the PySide6 GUI.
- `tests/`: model-free tests for the post-processing (`python -m pytest tests`).
- `install.py`, `setup.bat`/`setup.sh`, `run_gui.bat`/`run_gui.sh`: setup and launch scripts.
- `build_release.py`, `packaging/`: build the release packages and the icon generator.
  The Windows launcher is written in C# and compiled by the C# compiler built into Windows.
