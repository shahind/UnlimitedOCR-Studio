"""Markdown -> HTML with LaTeX math left intact for MathJax (used by the GUI preview and .html export)."""
from __future__ import annotations

import html
import re

import markdown

MATHJAX_CDN = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml-full.js"

# display first, then inline; \$ is a literal dollar
_MATH_RE = re.compile(
    r"(\$\$.+?\$\$|\\\[.+?\\\]|(?<![\\$])\$(?!\s)[^$\n]+?(?<![\s\\])\$(?!\d)|\\\(.+?\\\))",
    re.DOTALL)

CSS = """
:root { color-scheme: light dark; --fg:#1f2328; --bg:#ffffff; --muted:#656d76; --line:#d0d7de; --code:#f6f8fa; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --bg:#0d1117; --muted:#8d96a0; --line:#30363d; --code:#161b22; } }
body { font: 16px/1.6 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: var(--fg);
       background: var(--bg); max-width: 900px; margin: 0 auto; padding: 24px 32px 64px; }
h1, h2, h3 { line-height: 1.25; margin-top: 1.4em; }
h1, h2 { border-bottom: 1px solid var(--line); padding-bottom: .3em; }
table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 6px 12px; }
code, pre { background: var(--code); border-radius: 6px; font-family: Consolas, monospace; }
pre { padding: 12px; overflow-x: auto; }
img { max-width: 100%; border: 1px solid var(--line); border-radius: 4px; }
.page-sep { color: var(--muted); font-size: 12px; text-align: center; margin: 2em 0 1em;
            border-top: 1px dashed var(--line); padding-top: 4px; }
mjx-container[display="true"] { overflow-x: auto; overflow-y: hidden; }
"""


def md_to_html_body(md: str) -> str:
    stash: list[str] = []

    def keep(m: re.Match) -> str:
        stash.append(m.group(0))
        return f"\u0000MATH{len(stash) - 1}\u0000"

    protected = _MATH_RE.sub(keep, md)
    protected = re.sub(r"<!-- page (\d+) -->", r'<div class="page-sep">page \1</div>', protected)
    body = markdown.markdown(protected, extensions=["tables", "fenced_code", "sane_lists"])
    return re.sub(r"\u0000MATH(\d+)\u0000", lambda m: html.escape(stash[int(m.group(1))]), body)


def render_html(md: str, title: str = "Document") -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style>
<script>
window.MathJax = {{ tex: {{ inlineMath: [['$','$'],['\\\\(','\\\\)']], displayMath: [['$$','$$'],['\\\\[','\\\\]']],
                    processEscapes: true }}, svg: {{ fontCache: 'global' }} }};
</script>
<script async src="{MATHJAX_CDN}"></script>
</head><body>
{md_to_html_body(md)}
</body></html>
"""
