"""
Core engine: PDF/image -> Markdown with Baidu Unlimited-OCR (https://huggingface.co/baidu/Unlimited-OCR).

Usable on its own from the command line:
    python ocr_engine.py paper.pdf                 # writes paper.md + paper_images/
    python ocr_engine.py paper.pdf -o out/ --pages 1-5 --format txt

The GUI (app.py) drives the same OCREngine class.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# The model's remote code print()s and uses tqdm; under pythonw there is no stdout.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import pymupdf as fitz
from PIL import Image

DEFAULT_MODEL = "baidu/Unlimited-OCR"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
EOS_STR = "<｜end▁of▁sentence｜>"
PROMPT_BUDGET = 1600   # image tokens of one tiled page; generate(max_length=) counts prompt + output

# ----------------------------------------------------------------------------------------------
# Settings / result containers
# ----------------------------------------------------------------------------------------------

MODES = {
    # name: (description, per_page, base_size, image_size, crop_mode, ngram_window)
    "gundam": ("Page by page, tiled (best quality)", True, 1024, 640, True, 128),
    "base": ("Page by page, single view (faster)", True, 1024, 1024, False, 128),
    "multi": ("Multi-page chunks (cross-page context)", False, 1024, 1024, False, 1024),
}


@dataclass
class OCRSettings:
    mode: str = "gundam"
    dpi: int = 300
    max_new_tokens: int = 8192          # per page (per chunk in multi mode: x pages)
    pages_per_chunk: int = 6            # multi mode only
    no_repeat_ngram: int = 35
    dollar_math: bool = True            # \( \) -> $ $,  \[ \] -> $$ $$
    page_markers: bool = True           # <!-- page N --> comments between pages
    extract_figures: bool = True        # crop detected figures into image files
    headings: bool = True               # layout 'title' blocks -> Markdown '#' headings
    drop_headers: bool = True           # drop running headers, footers and page numbers


@dataclass
class PageResult:
    index: int                          # 0-based page index in the document
    markdown: str
    raw: str
    seconds: float = 0.0
    tokens: int = 0


@dataclass
class DocResult:
    source: Path
    work_dir: Path                      # holds images/ referenced by the markdown
    pages: list[PageResult] = field(default_factory=list)
    settings: OCRSettings = field(default_factory=OCRSettings)

    def markdown(self) -> str:
        parts = []
        for p in sorted(self.pages, key=lambda p: p.index):
            if self.settings.page_markers:
                parts.append(f"<!-- page {p.index + 1} -->")
            parts.append(p.markdown.strip())
        return "\n\n".join(parts).strip() + "\n"

    def plain_text(self) -> str:
        text = self.markdown()
        text = re.sub(r"<!-- page (\d+) -->", r"----- page \1 -----", text)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)\n?", "", text)
        return text


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------------------------
# Input handling
# ----------------------------------------------------------------------------------------------

def parse_page_range(spec: str, n_pages: int) -> list[int]:
    """'1-3,7,10-' -> 0-based page indices. Empty spec -> all pages."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(n_pages))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else n_pages
        else:
            start = end = int(part)
        for p in range(max(1, start), min(n_pages, end) + 1):
            if p - 1 not in pages:
                pages.append(p - 1)
    if not pages:
        raise ValueError(f"Page range '{spec}' selects no pages (document has {n_pages}).")
    return pages


def count_pages(path: Path) -> int:
    if path.suffix.lower() in IMAGE_EXTS:
        return 1
    with fitz.open(path) as doc:
        return doc.page_count


def render_pages(path: Path, pages: Iterable[int], dpi: int, out_dir: Path) -> dict[int, Path]:
    """Render selected PDF pages (or copy an image) to PNG files; returns {page_index: png_path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, Path] = {}
    if path.suffix.lower() in IMAGE_EXTS:
        img = Image.open(path).convert("RGB")
        dst = out_dir / "page_0001.png"
        img.save(dst)
        return {0: dst}
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    with fitz.open(path) as doc:
        for i in pages:
            dst = out_dir / f"page_{i + 1:04d}.png"
            if not dst.exists():
                doc[i].get_pixmap(matrix=mat, alpha=False).save(dst)
            result[i] = dst
    return result


# ----------------------------------------------------------------------------------------------
# Post-processing of raw model output
# ----------------------------------------------------------------------------------------------

DET_RE = re.compile(r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]*\])?\s*<\|/det\|>")
REF_RE = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.DOTALL)
FIGURE_LABELS = {"image", "chart", "figure", "picture", "photo", "diagram", "graphic", "seal"}
FURNITURE_LABELS = {"header", "footer", "page_number", "page_header", "page_footer"}
HEADING_LABELS = {"doc_title": "# ", "title": "## ", "paragraph_title": "## ", "section_title": "## ",
                  "sub_title": "### ", "subtitle": "### ", "heading": "## "}


def _parse_boxes(box_text: str | None) -> list[list[float]]:
    if not box_text:
        return []
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", box_text)]
    return [nums[i:i + 4] for i in range(0, len(nums) - 3, 4)]


def convert_math_delimiters(md: str) -> str:
    r"""\[..\] -> $$..$$ and \(..\) -> $..$ (skipping LaTeX line breaks like '\\[2pt]')."""
    md = re.sub(r"(?<!\\)\\\[(.+?)(?<!\\)\\\]", lambda m: "$$" + m.group(1).strip("\n") + "$$", md, flags=re.DOTALL)
    md = re.sub(r"(?<!\\)\\\((.+?)(?<!\\)\\\)", lambda m: "$" + m.group(1).strip() + "$", md, flags=re.DOTALL)
    # display math on its own lines renders more reliably in most Markdown viewers
    md = re.sub(r"[ \t]*\$\$(.+?)\$\$[ \t]*", lambda m: "\n$$\n" + m.group(1).strip() + "\n$$\n", md, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", md)


def postprocess(raw: str, page_image: Image.Image | None, images_dir: Path, prefix: str,
                settings: OCRSettings) -> str:
    """Turn raw model output (with <|det|> layout markers) into clean Markdown.

    Figures detected as image/chart/... blocks are cropped from the page and linked as images/<prefix>N.png.
    """
    raw = raw.replace(EOS_STR, "").strip()
    raw = REF_RE.sub(lambda m: m.group(1), raw)

    blocks: list[str] = []
    label, boxes, fig_idx = None, [], 0
    matches = list(DET_RE.finditer(raw))

    def flush(content: str):
        nonlocal fig_idx
        if settings.drop_headers and label in FURNITURE_LABELS:
            return
        content = "\n".join(line.rstrip() for line in content.strip("\n").splitlines()).strip()
        if (settings.headings and label in HEADING_LABELS and content and "\n" not in content
                and not content.startswith("#")):
            content = HEADING_LABELS[label] + content
        if label in FIGURE_LABELS:
            links = []
            if settings.extract_figures and page_image is not None:
                w, h = page_image.size
                for b in boxes:
                    x1, y1, x2, y2 = (int(b[0] / 999 * w), int(b[1] / 999 * h),
                                      int(b[2] / 999 * w), int(b[3] / 999 * h))
                    if x2 - x1 < 4 or y2 - y1 < 4:
                        continue
                    name = f"{prefix}fig{fig_idx + 1}.png"
                    images_dir.mkdir(parents=True, exist_ok=True)
                    page_image.crop((x1, y1, x2, y2)).save(images_dir / name)
                    links.append(f"![](images/{name})")
                    fig_idx += 1
            content = "\n\n".join(links + ([content] if content else []))
        if content:
            blocks.append(content)

    if not matches:
        blocks.append(raw)
    else:
        if matches[0].start() > 0:
            flush(raw[:matches[0].start()])
        for i, m in enumerate(matches):
            label, boxes = m.group(1), _parse_boxes(m.group(2))
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            flush(raw[m.end():end])

    md = "\n\n".join(blocks)
    md = md.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
    if settings.dollar_math:
        md = convert_math_delimiters(md)
    return md.strip()


# ----------------------------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------------------------

class OCREngine:
    """Loads Unlimited-OCR once and converts documents page by page (or in multi-page chunks).

    Callbacks (all optional, called from the worker thread):
      on_status(str)                                   human-readable status line
      on_page_start(done, total, page_index, png_path) before a page/chunk starts
      on_token(str)                                    streamed text as it is generated
      on_page_done(PageResult, done, total)            after each page (per chunk page in multi mode)
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self.model = None
        self.tokenizer = None
        self._cancel = threading.Event()
        self._on_token: Callable[[str], None] | None = None
        self._token_count = 0
        self._saw_prompt = False

    # -- model ---------------------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.model is not None

    def load(self, on_status: Callable[[str], None] = print):
        if self.loaded:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        if not torch.cuda.is_available():
            raise RuntimeError(
                "No CUDA GPU available to PyTorch. Unlimited-OCR's code only runs on NVIDIA GPUs.\n"
                f"torch {torch.__version__} — install a CUDA build, e.g.:\n"
                "pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128")
        on_status(f"Loading {self.model_name} (first run downloads ~6.7 GB)...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        model = AutoModel.from_pretrained(
            self.model_name, trust_remote_code=True, use_safetensors=True,
            dtype=torch.bfloat16, attn_implementation="eager")
        self.model = model.eval().cuda()
        self._install_generate_hook()
        gpu = torch.cuda.get_device_name(0)
        mem = torch.cuda.memory_allocated() / 2**30
        on_status(f"Model ready on {gpu} ({mem:.1f} GB VRAM used)")

    def _install_generate_hook(self):
        """infer()/infer_multi() call self.generate internally; wrap it to add live streaming
        and a cancel check without modifying the model's remote code."""
        from transformers import StoppingCriteria, StoppingCriteriaList, TextStreamer

        engine = self

        class _Streamer(TextStreamer):
            def put(self, value):
                if engine._saw_prompt:
                    engine._token_count += value.numel() if hasattr(value, "numel") else 1
                engine._saw_prompt = True
                super().put(value)

            def on_finalized_text(self, text: str, stream_end: bool = False):
                text = text.replace(EOS_STR, "")
                if text and engine._on_token:
                    engine._on_token(text)

        class _Cancel(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return engine._cancel.is_set()

        original = self.model.generate

        def generate(*args, **kwargs):
            engine._saw_prompt = False
            kwargs["streamer"] = _Streamer(self.tokenizer, skip_prompt=True, skip_special_tokens=False)
            crit = kwargs.get("stopping_criteria") or StoppingCriteriaList()
            crit.append(_Cancel())
            kwargs["stopping_criteria"] = crit
            return original(*args, **kwargs)

        self.model.generate = generate

    def cancel(self):
        self._cancel.set()

    def unload(self):
        import torch
        self.model = self.tokenizer = None
        torch.cuda.empty_cache()

    # -- conversion ----------------------------------------------------------------------------
    def convert(self, source: str | Path, settings: OCRSettings | None = None, page_spec: str = "",
                work_dir: str | Path | None = None,
                on_status: Callable[[str], None] = lambda s: None,
                on_page_start: Callable[[int, int, int, Path], None] = lambda *a: None,
                on_token: Callable[[str], None] | None = None,
                on_page_done: Callable[[PageResult, int, int], None] = lambda *a: None) -> DocResult:
        settings = settings or OCRSettings()
        if settings.mode not in MODES:
            raise ValueError(f"Unknown mode {settings.mode!r}; choose from {list(MODES)}")
        source = Path(source)
        self.load(on_status)
        self._cancel.clear()
        self._on_token = on_token

        work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="uocr_"))
        pages_dir, images_dir, scratch = work / "pages", work / "images", work / "_model_out"
        doc = DocResult(source=source, work_dir=work, settings=settings)

        n_pages = count_pages(source)
        selected = parse_page_range(page_spec, n_pages)
        on_status(f"Rendering {len(selected)} page(s) of {source.name} at {settings.dpi} DPI...")
        pngs = render_pages(source, selected, settings.dpi, pages_dir)

        _, per_page, base_size, image_size, crop_mode, window = MODES[settings.mode]
        total = len(selected)
        if per_page:
            for done, pi in enumerate(selected):
                self._check_cancel()
                on_page_start(done, total, pi, pngs[pi])
                on_status(f"{source.name}: page {pi + 1} ({done + 1}/{total})")
                t0, self._token_count = time.time(), 0
                raw = self.model.infer(
                    self.tokenizer, prompt="<image>document parsing.", image_file=str(pngs[pi]),
                    output_path=str(scratch), base_size=base_size, image_size=image_size,
                    crop_mode=crop_mode, eval_mode=True,
                    max_length=min(32768, settings.max_new_tokens + PROMPT_BUDGET),
                    no_repeat_ngram_size=settings.no_repeat_ngram, ngram_window=window)
                self._check_cancel()
                page = PageResult(pi, self._post(raw, pngs[pi], images_dir, pi, settings), raw,
                                  time.time() - t0, self._token_count)
                doc.pages.append(page)
                on_page_done(page, done + 1, total)
        else:
            n = max(1, settings.pages_per_chunk)
            for c in range(0, total, n):
                self._check_cancel()
                chunk = selected[c:c + n]
                on_page_start(c, total, chunk[0], pngs[chunk[0]])
                on_status(f"{source.name}: pages {chunk[0] + 1}-{chunk[-1] + 1} ({c + len(chunk)}/{total})")
                t0, self._token_count = time.time(), 0
                raw, _ = self.model.infer_multi(
                    self.tokenizer, prompt="<image>Multi page parsing.",
                    image_files=[str(pngs[p]) for p in chunk], output_path=str(scratch),
                    image_size=image_size,
                    max_length=min(32768, (settings.max_new_tokens + 300) * len(chunk) + 64),
                    no_repeat_ngram_size=settings.no_repeat_ngram, ngram_window=window)
                self._check_cancel()
                parts = raw.split("<PAGE>")
                parts = [p for p in parts[1:]] if len(parts) > 1 else parts
                dt = (time.time() - t0) / len(chunk)
                for k, pi in enumerate(chunk):
                    text = parts[k] if k < len(parts) else ""
                    if k == len(chunk) - 1 and len(parts) > len(chunk):   # overflow goes to last page
                        text += "\n\n" + "\n\n".join(parts[len(chunk):])
                    page = PageResult(pi, self._post(text, pngs[pi], images_dir, pi, settings), text,
                                      dt, self._token_count // len(chunk))
                    doc.pages.append(page)
                    on_page_done(page, c + k + 1, total)
        shutil.rmtree(scratch, ignore_errors=True)
        on_status(f"Finished {source.name}: {total} page(s)")
        return doc

    def _check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled()

    @staticmethod
    def _post(raw: str, png: Path, images_dir: Path, page_index: int, settings: OCRSettings) -> str:
        with Image.open(png) as im:
            return postprocess(raw, im.convert("RGB"), images_dir, f"p{page_index + 1:03d}_", settings)


# ----------------------------------------------------------------------------------------------
# Saving
# ----------------------------------------------------------------------------------------------

def save_document(doc: DocResult, dest: str | Path, markdown_text: str | None = None) -> Path:
    """Save as .md (+ <stem>_images/), .txt, or .html (+ <stem>_images/), chosen by extension.

    markdown_text overrides the generated markdown (e.g. after edits in the GUI)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    md = markdown_text if markdown_text is not None else doc.markdown()
    ext = dest.suffix.lower()
    if ext == ".txt":
        text = md if markdown_text is not None else doc.plain_text()
        if markdown_text is not None:
            text = re.sub(r"!\[[^\]]*\]\([^)]*\)\n?", "", text)
            text = re.sub(r"<!-- page (\d+) -->", r"----- page \1 -----", text)
        dest.write_text(text, encoding="utf-8")
        return dest

    img_dir_name = f"{dest.stem}_images"
    used = sorted(set(re.findall(r"\]\(images/([^)\s]+)\)", md)))
    if used:
        out_img = dest.parent / img_dir_name
        out_img.mkdir(exist_ok=True)
        for name in used:
            src = doc.work_dir / "images" / name
            if src.exists():
                shutil.copy2(src, out_img / name)
        md = md.replace("](images/", f"]({img_dir_name}/")
    if ext == ".html":
        from md_render import render_html
        dest.write_text(render_html(md, title=doc.source.stem), encoding="utf-8")
    else:
        dest.write_text(md, encoding="utf-8")
    return dest


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF/image -> Markdown with Baidu Unlimited-OCR")
    ap.add_argument("inputs", nargs="+", help="PDF or image files")
    ap.add_argument("-o", "--out-dir", help="output folder (default: next to each input)")
    ap.add_argument("-f", "--format", choices=["md", "txt", "html"], default="md")
    ap.add_argument("--pages", default="", help="page range, e.g. '1-3,7,10-'")
    ap.add_argument("--mode", choices=list(MODES), default="gundam")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--max-tokens", type=int, default=8192, help="max tokens per page")
    ap.add_argument("--chunk", type=int, default=6, help="pages per chunk in multi mode")
    ap.add_argument("--keep-tex-delims", action="store_true", help=r"keep \( \) \[ \] instead of $ / $$")
    ap.add_argument("--no-page-markers", action="store_true")
    ap.add_argument("--no-headings", action="store_true", help="don't turn detected titles into # headings")
    ap.add_argument("--keep-headers", action="store_true", help="keep page headers/footers/page numbers")
    ap.add_argument("--no-figures", action="store_true", help="don't crop detected figures into images")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="HF repo id or local folder")
    ap.add_argument("-q", "--quiet", action="store_true", help="don't stream text to the console")
    a = ap.parse_args(argv)

    settings = OCRSettings(mode=a.mode, dpi=a.dpi, max_new_tokens=a.max_tokens, pages_per_chunk=a.chunk,
                           dollar_math=not a.keep_tex_delims, page_markers=not a.no_page_markers,
                           headings=not a.no_headings, extract_figures=not a.no_figures,
                           drop_headers=not a.keep_headers)
    engine = OCREngine(a.model)
    for inp in a.inputs:
        src = Path(inp)
        out_dir = Path(a.out_dir) if a.out_dir else src.parent
        dest = out_dir / f"{src.stem}.{a.format}"
        doc = engine.convert(
            src, settings, a.pages,
            on_status=lambda s: print(f"\n[status] {s}", flush=True),
            on_token=None if a.quiet else (lambda t: print(t, end="", flush=True)),
            on_page_done=lambda p, d, t: print(f"\n[page {p.index + 1}] {p.tokens} tokens, "
                                               f"{p.seconds:.1f}s ({d}/{t})", flush=True))
        print(f"\nSaved {save_document(doc, dest)}")
        shutil.rmtree(doc.work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
