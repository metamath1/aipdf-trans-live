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


def _scan_inline_dollar_math(text: str, store: dict, counter: list) -> str:
    """문자 단위 스캔으로 $…$ 인라인 수식을 추출·플레이스홀더로 치환한다.

    regex 방식의 엣지 케이스(유니코드 공백, Windows 개행, 특수 선행문자 등)를
    완전히 우회한다. 비탐욕(non-greedy): 항상 가장 가까운 닫는 $를 선택.

    규칙:
    - $$ 는 건너뜀 (display math는 이미 처리됨)
    - $ 뒤 첫 문자가 공백(Unicode 포함) 또는 십진수이면 수식 아님 ($10, $ price)
    - 빈 줄(\\n\\n)을 만나면 닫는 $ 탐색 중단
    """
    out: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch != '$':
            out.append(ch)
            i += 1
            continue

        # '$' 발견
        # Guard 0: ASCII 알파뉴메릭 또는 LaTeX 닫는 기호({, ), ]) 뒤 '$' 는
        #          닫는 구분자 → 새 여는 구분자로 처리하지 않음.
        #          예: "$1 < t \leq T$에 대한" 에서 T 뒤의 $가 여는 기호로
        #          잘못 처리되어 이후 수식이 깨지는 문제 방지.
        #          (Korean/Unicode 문자는 isalnum()이 True여도 여기서는 제외)
        if i > 0 and (
            'a' <= text[i - 1] <= 'z'
            or 'A' <= text[i - 1] <= 'Z'
            or '0' <= text[i - 1] <= '9'
            or text[i - 1] in '})]'
        ):
            out.append(ch)
            i += 1
            continue

        # Guard 1: '$$' 는 건너뜀 (display math placeholder 뒤 남은 $ 포함)
        if i + 1 < n and text[i + 1] == '$':
            out.append(ch)
            i += 1
            continue

        # Guard 2: $ 바로 다음이 공백(Unicode 포함) 또는 십진수이면 수식 아님
        if i + 1 >= n or text[i + 1].isspace() or text[i + 1].isdecimal():
            out.append(ch)
            i += 1
            continue

        # 닫는 '$' 탐색 (빈 줄에서 중단)
        j = i + 1
        found_close = -1
        while j < n:
            c = text[j]
            if c == '$':
                if j + 1 < n and text[j + 1] == '$':
                    j += 2          # $$ 는 닫는 기호로 인정하지 않음
                    continue
                found_close = j
                break
            if c == '\n' and j + 1 < n and text[j + 1] == '\n':
                break               # 빈 줄 = 문단 경계 → 중단
            j += 1

        if found_close == -1:
            out.append(ch)
            i += 1
            continue

        # 수식 추출 ($ 구분자 포함)
        formula = text[i:found_close + 1]
        key = f"XAMATHINLNX{counter[0]:05d}XAMATHINLNX"
        store[key] = formula
        counter[0] += 1
        out.append(key)
        i = found_close + 1

    return ''.join(out)


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
    # Inline math: \(...\) form
    text = re.sub(r'\\\([\s\S]*?\\\)', save_inln, text)
    # Inline $...$: 문자 스캔 방식 — regex 엣지 케이스 완전 우회
    text = _scan_inline_dollar_math(text, store, counter)

    return text, store


def _restore_math(html: str, store: dict) -> str:
    """Restore math placeholders verbatim.

    The original delimiters ($...$, $$...$$, \\(...\\), \\[...\\]) are preserved
    exactly so MathJax can process them without any conversion errors.
    MathJax is configured to handle all four delimiter forms.

    '<' is HTML-escaped to '&lt;' to prevent the HTML parser from interpreting
    LaTeX operators (e.g. $a < b$) as tag openers. MathJax reads DOM text nodes
    where the entity has already been decoded back to '<', so rendering is correct.
    """
    for key, value in store.items():
        safe = value.replace('<', '&lt;')
        html = html.replace(key, safe)
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


def _protect_media_markers(text: str) -> tuple[str, dict]:
    """[TABLE_N] 및 [FIGURE_N] 마커를 mistune 처리 전에 추출한다.

    mistune은 [text] 형식을 링크 참조로 파싱할 수 있어 마커가 변형될 수 있다.
    _extract_math()와 동일한 방식으로 마커를 고유 키로 치환하여 보호하고,
    나중에 _restore_media_markers()로 복원한다.

    각 마커는 앞뒤에 빈 줄을 추가해 독립된 문단으로 보장한다.
    """
    import re as _re
    store: dict[str, str] = {}

    def _save_table(m: re.Match) -> str:
        n = int(m.group(1))
        key = f"XTABLEX{n:05d}XTABLEX"
        store[key] = f"[TABLE_{n}]"
        return f"\n\n{key}\n\n"

    def _save_figure(m: re.Match) -> str:
        n = int(m.group(1))
        key = f"XFIGUREX{n:05d}XFIGUREX"
        store[key] = f"[FIGURE_{n}]"
        return f"\n\n{key}\n\n"

    protected = _re.sub(r'\[TABLE_(\d+)\]', _save_table, text, flags=_re.IGNORECASE)
    protected = _re.sub(r'\[FIGURE_(\d+)\]', _save_figure, protected, flags=_re.IGNORECASE)
    return protected, store


def _restore_media_markers(html: str, store: dict) -> str:
    """XTABLEX / XFIGUREX 플레이스홀더를 원래 마커로 복원한다."""
    for key, value in store.items():
        html = html.replace(key, value)
    return html


# 하위 호환 별칭
def _protect_table_markers(text: str) -> tuple[str, dict]:
    return _protect_media_markers(text)


def _restore_table_markers(html: str, store: dict) -> str:
    return _restore_media_markers(html, store)


def markdown_with_tables_to_pdf_bytes(
    markdown_text: str,
    table_images: list,
    layout: str = "single",
    figure_images: list | None = None,
) -> bytes:
    """표·그림 이미지가 포함된 마크다운 → PDF bytes.

    [TABLE_N] / [FIGURE_N] 마커를 mistune 전에 추출(보호)하고, mistune 이후
    복원한 뒤 실제 이미지 태그로 교체한다. mistune의 링크 파싱 간섭을 방지.

    Args:
        markdown_text:  [TABLE_0] / [FIGURE_0] … 플레이스홀더가 포함된 마크다운
        table_images:   PIL.Image 리스트 (TABLE_0, TABLE_1, …에 대응)
        layout:         AI가 분석한 표 레이아웃 ("single" | "2col" | "stacked" | "mixed")
        figure_images:  PIL.Image 리스트 (FIGURE_0, FIGURE_1, …에 대응). None이면 그림 없음.
    """
    from src.table_handler import inject_table_images, inject_figure_images

    # 1) [TABLE_N] / [FIGURE_N] 마커를 mistune 전에 키로 치환 (링크 파싱 방지)
    protected_md, media_store = _protect_media_markers(markdown_text)

    # 2) mistune + math 보호 파이프라인 (키는 평범한 텍스트로 통과)
    html = markdown_to_html(protected_md)

    # 3) 키 → [TABLE_N] / [FIGURE_N] 복원 (이 시점엔 반드시 <p>[...]</p> 형태)
    html = _restore_media_markers(html, media_store)

    # 4) [TABLE_N] → 레이아웃 반영 이미지 태그 교체
    html = inject_table_images(html, table_images, layout)

    # 5) [FIGURE_N] → 이미지 태그 교체
    if figure_images:
        html = inject_figure_images(html, figure_images)

    return asyncio.run(_playwright_to_pdf(html))


def open_in_browser(markdown_text: str) -> str:
    """Save Markdown as HTML to a temp file and open in default browser.

    Returns the path to the saved HTML file.
    """
    html = markdown_to_html(markdown_text)
    tmp_path = Path(tempfile.gettempdir()) / "aipdf_translation.html"
    tmp_path.write_text(html, encoding="utf-8")
    webbrowser.open(tmp_path.as_uri())
    return str(tmp_path)
