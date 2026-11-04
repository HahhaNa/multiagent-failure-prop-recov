"""Render a Markdown writeup to PDF: US letter, 7 x 9 in text block, two columns, 10/12 pt Times, numbered pages.
Text after the first horizontal rule (the appendix) is set in one column. Needs google-chrome.
Usage: uv run --with markdown python scripts/build_writeup.py docs/phase1_writeup.md docs/phase1_writeup.pdf"""

import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CSS = """
@page { size: letter; margin: 1in 0.75in; @bottom-center { content: counter(page); font: 9pt "Liberation Serif", Times, serif; } }
body { font: 10pt/12pt "Liberation Serif", "Times New Roman", Times, serif; color: #000; margin: 0; }
.cols { column-count: 2; column-gap: 0.25in; text-align: justify; hyphens: auto; }
h1 { font-size: 14pt; line-height: 16pt; text-align: center; margin: 0 0 8pt; }
h2 { font-size: 11pt; line-height: 13pt; margin: 8pt 0 3pt; break-after: avoid; }
p { margin: 0 0 4pt; }
ul, ol { margin: 0 0 4pt; padding-left: 13pt; }
li { margin: 0 0 1.5pt; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0 5pt; font-size: 8.5pt; line-height: 10pt; break-inside: avoid; text-align: left; }
th, td { border-top: 0.5pt solid #000; border-bottom: 0.5pt solid #000; padding: 1.5pt 3pt; vertical-align: top; }
th { font-weight: bold; }
img { width: 100%; break-inside: avoid; }
.wide { column-span: all; margin: 6pt 0 8pt; }
figure { margin: 3pt 0 6pt; break-inside: avoid; } figcaption { font-size: 8.5pt; line-height: 10pt; }
code { font-size: 8.5pt; }
.appendix { break-before: page; }
"""


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    text = src.read_text()
    title, _, rest = text.partition("\n")
    main_md, _, appendix_md = rest.partition("\n---\n")
    md = lambda s: markdown.markdown(s, extensions=["tables"])
    html = (
        f"<!doctype html><html><head><meta charset='utf-8'><base href='{src.resolve().parent.as_uri()}/'><style>{CSS}</style></head><body>"
        f"{md(title)}<div class='cols'>{md(main_md)}</div>"
        + (f"<div class='appendix'>{md(appendix_md)}</div>" if appendix_md else "")
        + "</body></html>"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
    subprocess.run(["google-chrome", "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={out}", f.name], check=True, capture_output=True)
    print(out)


if __name__ == "__main__":
    main()
