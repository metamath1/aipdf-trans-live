"""Markdown + LaTeX → HTML with MathJax, saved to temp file for browser viewing.
Also supports rendering to PDF bytes via playwright (system Edge/Chrome).
"""

import asyncio
import re
import tempfile
import webbrowser
from pathlib import Path

import mistune

# Use a sentinel comment so we can replace without .format()
# (LaTeX content contains { } which would break str.format())
_BODY_SENTINEL = "<!--TRANSLATION_BODY-->"

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>번역 결과</title>
<script>
MathJax = {
  tex: {
    inlineMath:  [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true
  },
  options: { skipHtmlTags: ['script','noscript','style','textarea','pre'] }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>
  body {
    font-family: '맑은 고딕', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
    font-size: 15px;
    line-height: 1.85;
    max-width: 860px;
    margin: 40px auto;
    padding: 0 24px 60px;
    color: #1a1a1a;
    background: #fafafa;
  }
  h1, h2, h3, h4 {
    color: #1a1a2e;
    border-bottom: 1px solid #ddd;
    padding-bottom: 4px;
    margin-top: 1.4em;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    margin: 14px 0;
    font-size: 14px;
  }
  th, td {
    border: 1px solid #bbb;
    padding: 7px 12px;
    text-align: left;
  }
  th { background: #f0f0f0; font-weight: bold; }
  tr:nth-child(even) { background: #f8f8f8; }
  code {
    background: #f0f0f0;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: 'Consolas', monospace;
    font-size: 0.9em;
  }
  pre {
    background: #f0f0f0;
    padding: 14px;
    border-radius: 5px;
    overflow-x: auto;
    font-size: 0.88em;
  }
  blockquote {
    border-left: 4px solid #ccc;
    margin: 0;
    padding: 4px 16px;
    color: #555;
  }
  p { margin: 0.6em 0; }
  .mjx-chtml { font-size: 1.05em !important; }
</style>
</head>
<body>
<!--TRANSLATION_BODY-->
</body>
</html>"""


def _extract_math(text: str) -> tuple[str, dict]:
    """Replace math expressions with safe ASCII placeholders before mistune.

    Handles $$...$$, $...$, \\[...\\], and \\(...\\) notations so mistune
    cannot mangle _ * ^ inside LaTeX as Markdown emphasis/superscript.
    Returns (modified_text, placeholder_dict).
    """
    store: dict[str, str] = {}
    counter = [0]

    def save_disp(m: re.Match) -> str:
        key = f"XAMATHDISPX{counter[0]:05d}XAMATHDISPX"
        store[key] = m.group(0)
        counter[0] += 1
        return key

    def save_inln(m: re.Match) -> str:
        key = f"XAMATHINLNX{counter[0]:05d}XAMATHINLNX"
        store[key] = m.group(0)
        counter[0] += 1
        return key

    # Display math first (may span multiple lines)
    text = re.sub(r'\$\$[\s\S]*?\$\$', save_disp, text)
    text = re.sub(r'\\\[[\s\S]*?\\\]', save_disp, text)
    # Inline math: \(...\) and $...$ forms
    text = re.sub(r'\\\([\s\S]*?\\\)', save_inln, text)
    # Inline $...$: NOT followed by digit/space/newline (avoids $10, $ price etc.)
    text = re.sub(r'\$(?=[^\$\s\d\n])([^\$\n]*?)\$', save_inln, text)

    return text, store


def _restore_math(html: str, store: dict) -> str:
    """Restore math placeholders verbatim.

    The original delimiters ($...$, $$...$$, \\(...\\), \\[...\\]) are preserved
    exactly so MathJax can process them without any conversion errors.
    MathJax is configured to handle all four delimiter forms.
    """
    for key, value in store.items():
        html = html.replace(key, value)
    return html


def markdown_to_html(markdown_text: str) -> str:
    """Markdown (with LaTeX math) → full HTML string with MathJax support."""
    protected, store = _extract_math(markdown_text)
    body = mistune.html(protected)
    body = _restore_math(body, store)
    # Use replace() instead of .format() — LaTeX { } would break format()
    return HTML_TEMPLATE.replace(_BODY_SENTINEL, body)


def markdown_to_pdf_bytes(markdown_text: str) -> bytes:
    """Markdown → PDF bytes with MathJax math rendered via playwright.

    Uses system Edge/Chrome if available; falls back to playwright Chromium.
    """
    html = markdown_to_html(markdown_text)
    return asyncio.run(_playwright_to_pdf(html))


async def _playwright_to_pdf(html: str) -> bytes:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await _launch_browser(p)
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        # Wait for MathJax to fully initialise and typeset all elements
        await page.evaluate(
            """async () => {
                if (window.MathJax) {
                    // Wait for startup (script load + config) to finish
                    if (window.MathJax.startup && window.MathJax.startup.promise) {
                        await window.MathJax.startup.promise;
                    }
                    // Explicit full typeset pass
                    if (window.MathJax.typesetPromise) {
                        await window.MathJax.typesetPromise();
                    }
                }
            }"""
        )
        # Small buffer: let the rendering engine commit all layout changes
        await page.wait_for_timeout(600)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm",
                    "left": "18mm", "right": "18mm"},
        )
        await browser.close()
    return pdf_bytes


async def _launch_browser(p):
    """Try system Edge → Chrome → playwright Chromium in order."""
    for channel in ("msedge", "chrome"):
        try:
            return await p.chromium.launch(channel=channel, headless=True)
        except Exception:
            pass
    return await p.chromium.launch(headless=True)


def markdown_with_tables_to_pdf_bytes(
    markdown_text: str,
    table_images: list,
    layout: str = "single",
) -> bytes:
    """표 이미지가 포함된 마크다운 → PDF bytes.

    markdown_to_html()로 HTML을 생성한 뒤 [TABLE_N] 플레이스홀더를
    base64 인라인 이미지로 교체하고 playwright로 PDF를 렌더링한다.

    Args:
        markdown_text: [TABLE_0] … 플레이스홀더가 포함된 마크다운 문자열
        table_images:  PIL.Image 리스트 (순서대로 TABLE_0, TABLE_1, …에 대응)
        layout:        AI가 분석한 레이아웃 ("single" | "2col" | "stacked" | "mixed")
    """
    from src.table_handler import inject_table_images
    html = markdown_to_html(markdown_text)                    # 기존 math 보호 로직 재사용
    html = inject_table_images(html, table_images, layout)    # 레이아웃 반영 이미지 삽입
    return asyncio.run(_playwright_to_pdf(html))              # 기존 playwright 파이프라인 재사용


def open_in_browser(markdown_text: str) -> str:
    """Save Markdown as HTML to a temp file and open in default browser.

    Returns the path to the saved HTML file.
    """
    html = markdown_to_html(markdown_text)
    tmp_path = Path(tempfile.gettempdir()) / "aipdf_translation.html"
    tmp_path.write_text(html, encoding="utf-8")
    webbrowser.open(tmp_path.as_uri())
    return str(tmp_path)
