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

def crop_table_pct(
    image: Image.Image,
    x_pct: float,
    y_pct: float,
    w_pct: float,
    h_pct: float,
) -> Image.Image:
    """PIL 이미지에서 퍼센트 좌표로 표 영역을 크롭한다.

    AI가 반환한 x_pct/y_pct/w_pct/h_pct (이미지 크기 대비 0~100 비율)를
    픽셀 좌표로 변환하여 크롭한 PIL.Image를 반환한다.

    Args:
        image: 전체 선택 영역의 고해상도 PIL 이미지
        x_pct: 표 왼쪽 x좌표 / 이미지 너비 × 100
        y_pct: 표 위쪽  y좌표 / 이미지 높이 × 100
        w_pct: 표 너비   / 이미지 너비  × 100
        h_pct: 표 높이   / 이미지 높이  × 100
    """
    W, H = image.size
    x0 = max(0, int(x_pct / 100 * W))
    y0 = max(0, int(y_pct / 100 * H))
    x1 = min(W, int((x_pct + w_pct) / 100 * W))
    y1 = min(H, int((y_pct + h_pct) / 100 * H))
    return image.crop((x0, y0, x1, y1))


def inject_table_images(
    html: str,
    table_images: list[Image.Image],
    layout: str = "single",
) -> str:
    """HTML 내 ``<p>[TABLE_N]</p>`` 패턴을 레이아웃에 맞는 이미지 태그로 교체한다.

    AI가 [TABLE_0], [TABLE_1] … 플레이스홀더를 출력하면 mistune이 이를
    ``<p>[TABLE_0]</p>`` 형태의 HTML로 변환한다. 이 함수는 그 패턴을 찾아
    layout에 따라 적절한 HTML 구조로 치환한다.

    Args:
        html:         mistune 변환 후의 HTML 문자열
        table_images: PIL.Image 리스트 (순서대로 TABLE_0, TABLE_1, …에 대응)
        layout:       AI가 분석한 레이아웃 ("single" | "2col" | "stacked" | "mixed")

    Returns:
        표 이미지가 삽입된 HTML 문자열
    """
    def _to_b64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _figure(b64: str) -> str:
        return (
            f'<figure style="margin:0;flex:1;min-width:0;">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="max-width:100%;border:1px solid #d0d0d0;'
            f'border-radius:3px;box-shadow:0 1px 4px rgba(0,0,0,.12);">'
            f'</figure>'
        )

    def _figure_block(b64: str) -> str:
        return (
            f'<figure style="margin:1.2em 0;text-align:center;">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="max-width:100%;border:1px solid #d0d0d0;'
            f'border-radius:3px;box-shadow:0 1px 4px rgba(0,0,0,.12);">'
            f'</figure>'
        )

    # 2-column: [TABLE_0] + [TABLE_1]을 flex 컨테이너로 묶어 좌우 배치
    if layout == "2col" and len(table_images) >= 2:
        pair_pattern = re.compile(
            r'<p>\s*\[TABLE_0\]\s*</p>\s*<p>\s*\[TABLE_1\]\s*</p>',
            re.IGNORECASE,
        )
        flex_html = (
            '<div style="display:flex;gap:12px;align-items:flex-start;'
            'margin:1.2em 0;">'
            + _figure(_to_b64(table_images[0]))
            + _figure(_to_b64(table_images[1]))
            + '</div>'
        )
        html = pair_pattern.sub(flex_html, html)
        # 남은 마커(3개 이상인 경우) 순차 처리
        for i, img in enumerate(table_images[2:], start=2):
            pat = re.compile(rf'<p>\s*\[TABLE_{i}\]\s*</p>', re.IGNORECASE)
            html = pat.sub(_figure_block(_to_b64(img)), html)
        return html

    # single / stacked / mixed: 순차 치환 (기존 동작)
    for i, img in enumerate(table_images):
        pat = re.compile(rf'<p>\s*\[TABLE_{i}\]\s*</p>', re.IGNORECASE)
        html = pat.sub(_figure_block(_to_b64(img)), html)

    return html
