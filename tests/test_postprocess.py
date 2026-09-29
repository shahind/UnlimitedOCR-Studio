"""Model-free tests for the Markdown post-processing (run: python -m pytest tests)."""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr_engine import OCRSettings, convert_math_delimiters, parse_page_range, postprocess  # noqa: E402

RAW = r"""<|det|>header [10, 10, 900, 20]<|/det|>IEEE TRANSACTIONS ON X
<|det|>title [10, 30, 400, 40]<|/det|>I. Introduction
<|det|>text [10, 50, 900, 90]<|/det|>We have \(x^2\) and a matrix
\[
\begin{array}{c} a \\[2pt] b \end{array} \tag{1}
\]
<|det|>image [100, 100, 500, 500]<|/det|>
<|det|>image_caption [100, 510, 500, 520]<|/det|>Fig. 1. A figure.
<|det|>page_number [900, 980, 950, 990]<|/det|>12<｜end▁of▁sentence｜>"""


def test_postprocess_structure(tmp_path):
    page = Image.new("RGB", (1000, 1400), "white")
    md = postprocess(RAW, page, tmp_path, "p001_", OCRSettings())
    assert "IEEE" not in md and "\n12" not in md          # header / page number dropped
    assert md.startswith("## I. Introduction")
    assert "$x^2$" in md
    assert "$$\n\\begin{array}{c} a \\\\[2pt] b \\end{array} \\tag{1}\n$$" in md
    assert "![](images/p001_fig1.png)" in md and (tmp_path / "p001_fig1.png").exists()
    assert "end▁of▁sentence" not in md


def test_postprocess_options_off(tmp_path):
    s = OCRSettings(dollar_math=False, headings=False, drop_headers=False, extract_figures=False)
    md = postprocess(RAW, None, tmp_path, "p001_", s)
    assert "IEEE TRANSACTIONS" in md and md.splitlines()[2] == "I. Introduction"
    assert r"\(x^2\)" in md and "![](" not in md


def test_math_delimiters_keep_linebreak_spacing():
    assert convert_math_delimiters(r"a \(b\) c") == "a $b$ c"
    assert r"\\[4pt]" in convert_math_delimiters(r"\[ x \\[4pt] y \]")


def test_page_range():
    assert parse_page_range("", 3) == [0, 1, 2]
    assert parse_page_range("1-3,7,10-", 12) == [0, 1, 2, 6, 9, 10, 11]
    assert parse_page_range("2,2,1", 5) == [1, 0]
