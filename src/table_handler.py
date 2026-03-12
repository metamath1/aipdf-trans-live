"""table_handler.py — PyMuPDF 기반 표 감지·이미지 추출·HTML 주입 유틸리티."""
from __future__ import annotations

import base64
import io
import re

import fitz  # PyMuPDF ≥ 1.23.0 (find_tables 지원)
from PIL import Image


# ---------------------------------------------------------------------------
# 표 감지 및 이미지 렌더링
# ---------------------------------------------------------------------------

def detect_tables(page: fitz.Page, pdf_rect: fitz.Rect) -> list[fitz.Rect]:
    """선택 영역(pdf_rect) 내 표 bbox 목록을 y좌표 순으로 반환.

    PyMuPDF의 page.find_tables()를 사용하며, 각 표 주변에 4pt 여백을 더해
    테두리가 잘리지 않도록 확장한 뒤 페이지 범위로 클램프한다.
    """
    try:
        tab_finder = page.find_tables(clip=pdf_rect)
    except Exception:
        return []

    rects: list[fitz.Rect] = []
    for tab in tab_finder.tables:
        r = fitz.Rect(tab.bbox)
        # 테두리 여백 4pt 확장, 페이지 범위 클램프
        r = fitz.Rect(r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4) & page.rect
        if not r.is_empty:
            rects.append(r)

    return sorted(rects, key=lambda r: r.y0)


def render_table_image(
    page: fitz.Page, rect: fitz.Rect, dpi: int = 150
) -> Image.Image:
    """PDF 페이지의 rect 영역을 고해상도 PIL 이미지로 추출한다.

    Args:
        page: fitz.Page 객체
        rect: 추출할 영역 (PDF 포인트 좌표)
        dpi:  출력 해상도 (기본 150 DPI — 화면 표시에 충분히 선명)

    Returns:
        RGB PIL.Image
    """
    scale = dpi / 72  # PDF 기본 단위(point)는 72 DPI
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ---------------------------------------------------------------------------
# HTML 내 플레이스홀더 → <figure><img> 교체
# ---------------------------------------------------------------------------

def inject_table_images(html: str, table_images: list[Image.Image]) -> str:
    """HTML 내 ``<p>[TABLE_N]</p>`` 패턴을 base64 인라인 이미지 태그로 교체한다.

    AI가 [TABLE_0], [TABLE_1] … 플레이스홀더를 출력하면 mistune이 이를
    ``<p>[TABLE_0]</p>`` 형태의 HTML로 변환한다. 이 함수는 그 패턴을 찾아
    실제 표 이미지(PNG base64)가 담긴 ``<figure>`` 태그로 치환한다.

    Args:
        html:         mistune 변환 후의 HTML 문자열
        table_images: PIL.Image 리스트 (detect_tables → render_table_image 결과)

    Returns:
        표 이미지가 삽입된 HTML 문자열
    """
    def _to_b64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    for i, img in enumerate(table_images):
        b64 = _to_b64(img)
        # mistune 출력: <p>[TABLE_0]</p> 또는 공백 포함 변형
        pattern = re.compile(
            rf'<p>\s*\[TABLE_{i}\]\s*</p>', re.IGNORECASE
        )
        replacement = (
            f'<figure style="margin:1.2em 0;text-align:center;">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="max-width:100%;border:1px solid #d0d0d0;'
            f'border-radius:3px;box-shadow:0 1px 4px rgba(0,0,0,.12);">'
            f'</figure>'
        )
        html = pattern.sub(replacement, html)

    return html
