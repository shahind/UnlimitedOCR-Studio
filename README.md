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
- Python 3.11–3.13.

## Setup

Run the setup script. It creates `.venv`, installs CUDA PyTorch and the requirements, and optionally downloads the
model:

```bash
python install.py --download-model
```

On Windows you can double-click **`setup.bat`** instead, which does the same thing.
Use `python install.py --no-venv` to install into your current environment.

Or do it manually:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

The first run downloads the model (~6.7 GB) to the Hugging Face cache.

## GUI

Double-click **`run_gui.bat`**, or run `.venv\Scripts\python app.py [files…]`.

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
.venv\Scripts\python ocr_engine.py paper.pdf                       # -> paper.md + paper_images/
.venv\Scripts\python ocr_engine.py a.pdf b.pdf -o out --format html
.venv\Scripts\python ocr_engine.py scan.pdf --pages 1-5 --mode base -q
```

Run `python ocr_engine.py -h` for all options (`--keep-tex-delims`, `--keep-headers`, `--no-figures`, …).

## Performance

On an RTX 4060 Laptop GPU, generation runs at about 30 tokens/s. A dense two-column journal page takes about 90 s,
and a simple page takes 10–20 s. The GUI keeps the model loaded between files.

## Files

- `ocr_engine.py`: model loading, PDF rendering, OCR, Markdown post-processing, saving, and the CLI.
- `md_render.py`: Markdown to HTML conversion with protected LaTeX (used by the preview and the HTML export).
- `app.py`: the PySide6 GUI.
- `tests/`: model-free tests for the post-processing (`python -m pytest tests`).
