"""PDF viewer with drag-to-select image capture and live translation."""

import tkinter as tk
import threading

import fitz  # PyMuPDF
from PIL import Image, ImageTk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from src.translator import (
    translate_region,
    translate_to_markdown,
)

_PAGE_GAP = 8  # 연속 스크롤 모드: 페이지 사이 여백(px)


def _detect_figure_layout(fig_meta: list) -> str:
    """그림 메타데이터(% 좌표)로 레이아웃을 추정한다.

    Returns:
        "2col"    — 첫 두 그림이 좌우 나란히 배치됨
        "stacked" — 그림들이 위아래 배치됨
        "single"  — 그림 1개
    """
    if len(fig_meta) < 2:
        return "single"
    f0, f1 = fig_meta[0], fig_meta[1]
    # y 시작점이 15% 이내로 가깝고, f0이 f1보다 확실히 왼쪽에 있으면 2col
    y_close = abs(f0.get("y_pct", 0) - f1.get("y_pct", 0)) < 15
    x0_end   = f0.get("x_pct", 0) + f0.get("w_pct", 50)
    x1_start = f1.get("x_pct", 50)
    x_separated = x0_end < x1_start + 5  # f0이 f1의 왼쪽
    if y_close and x_separated:
        return "2col"
    return "stacked"


class PDFViewer(ttk.Frame):
    """Scrollable PDF viewer with drag-to-select, image capture, and translation."""

    ZOOM = 1.5  # render scale

    def __init__(self, parent, pdf_path: str):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
        self.current_page = 0
        self.zoom = self.ZOOM

        self._pil_img: Image.Image | None = None  # 단일 페이지 모드 PIL 이미지
        self._drag_start = None
        self._drag_rect = None
        self._translation_result: tuple = (None, "")
        self._translation_mode = "markdown"

        # 연속 스크롤 모드 상태
        self._continuous_scroll: bool = False
        self._pil_pages: list[Image.Image] = []     # 전체 페이지 PIL 이미지 목록
        self._photo_refs: list = []                  # PhotoImage 참조 유지
        self._page_y_offsets: list[int] = []         # 각 페이지 캔버스 y 시작 좌표

        self._build_ui()
        # Defer first render so the canvas has a real width to fit into
        self.after(80, self._fit_to_width)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(side=TOP, fill=X)

        ttk.Button(
            toolbar, text="◀ 이전", command=self._prev_page,
            bootstyle="outline-light",
        ).pack(side=LEFT, padx=2, pady=2)

        ttk.Button(
            toolbar, text="다음 ▶", command=self._next_page,
            bootstyle="outline-light",
        ).pack(side=LEFT, padx=2, pady=2)

        self._page_label = ttk.Label(
            toolbar, text="", bootstyle="light", font=("맑은 고딕", 8)
        )
        self._page_label.pack(side=LEFT, padx=8)

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=4, pady=3)

        ttk.Button(
            toolbar, text="확대 +", command=self._zoom_in,
            bootstyle="outline-light",
        ).pack(side=LEFT, padx=2)

        ttk.Button(
            toolbar, text="축소 −", command=self._zoom_out,
            bootstyle="outline-light",
        ).pack(side=LEFT, padx=2)

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=4, pady=3)

        self._continuous_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="연속 스크롤",
            variable=self._continuous_var,
            command=self._on_continuous_toggle,
            bootstyle="toolbutton-secondary",
        ).pack(side=LEFT, padx=(2, 4))

        # canvas + scrollbars
        frame = ttk.Frame(self)
        frame.pack(fill=BOTH, expand=YES)

        self.canvas = tk.Canvas(
            frame, bg="#2b2b2b", cursor="crosshair", highlightthickness=0
        )
        hbar = ttk.Scrollbar(
            frame, orient=HORIZONTAL, command=self.canvas.xview,
            bootstyle="round-secondary",
        )
        vbar = ttk.Scrollbar(
            frame, orient=VERTICAL, command=self.canvas.yview,
            bootstyle="round-secondary",
        )
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        hbar.pack(side=BOTTOM, fill=X)
        vbar.pack(side=RIGHT, fill=Y)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=YES)

        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

        # Auto-fit page to new panel width on resize
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel scroll / page turn
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)   # Windows / macOS
        self.canvas.bind("<Button-4>", self._on_mousewheel)     # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_mousewheel)     # Linux scroll down

        # Keyboard navigation (active after canvas gets focus)
        self.canvas.bind("<Down>",  lambda e: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Up>",    lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Next>",  lambda e: self._next_page())   # PgDn
        self.canvas.bind("<Prior>", lambda e: self._prev_page())   # PgUp
        self.canvas.bind("<Right>", lambda e: self._next_page())
        self.canvas.bind("<Left>",  lambda e: self._prev_page())

    # --------------------------------------------------------------- render --

    def _fit_to_width(self):
        """Set zoom so the current page fills the canvas width, then render.

        Retries after a short delay if the canvas hasn't been laid out yet.
        """
        w = self.canvas.winfo_width()
        if w > 10:
            page_w = self.doc[self.current_page].rect.width  # PDF points
            self.zoom = max(0.5, w / page_w)
            self._render_page()
        else:
            self.after(50, self._fit_to_width)

    def _on_canvas_configure(self, event):
        """Debounced handler: re-fit page width when the panel is resized."""
        if hasattr(self, "_resize_job"):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._fit_to_width)

    def _render_page(self):
        """현재 모드에 따라 단일 페이지 또는 전체 페이지를 렌더한다."""
        if self._continuous_scroll:
            self._render_all_pages()
            return

        page = self.doc[self.current_page]
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        self._pil_img = img  # store raw PIL image for cropping
        self._photo = ImageTk.PhotoImage(img)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=NW, image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, pix.width, pix.height))

        self._page_label.config(
            text=f"페이지 {self.current_page + 1} / {len(self.doc)}"
        )

    def _render_all_pages(self):
        """연속 스크롤 모드: 모든 페이지를 세로로 이어 붙여 캔버스에 렌더한다."""
        self.canvas.delete("all")
        self._pil_pages = []
        self._photo_refs = []
        self._page_y_offsets = []

        y = 0
        max_w = 1
        for i in range(len(self.doc)):
            page = self.doc[i]
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

            self._pil_pages.append(img)
            self._page_y_offsets.append(y)

            photo = ImageTk.PhotoImage(img)
            self._photo_refs.append(photo)
            self.canvas.create_image(0, y, anchor=NW, image=photo)

            max_w = max(max_w, pix.width)
            y += pix.height + _PAGE_GAP

        self.canvas.configure(scrollregion=(0, 0, max_w, y))
        self._update_page_label_continuous()

    def _on_continuous_toggle(self):
        """연속 스크롤 토글 처리."""
        self._continuous_scroll = self._continuous_var.get()
        if self._continuous_scroll:
            self._render_all_pages()
            # 현재 페이지 위치로 스크롤
            self.after(50, lambda: self._scroll_to_page(self.current_page, update_label=False))
        else:
            self._render_page()

    def _scroll_to_page(self, page_num: int, update_label: bool = True):
        """연속 모드에서 지정한 페이지 위치로 스크롤한다."""
        if not self._page_y_offsets or page_num >= len(self._page_y_offsets):
            return
        sr = self.canvas.cget("scrollregion")
        if not sr:
            return
        parts = str(sr).split()
        if len(parts) < 4:
            return
        total_h = float(parts[3])
        if total_h <= 0:
            return
        y_frac = self._page_y_offsets[page_num] / total_h
        self.canvas.yview_moveto(y_frac)
        if update_label:
            self.current_page = page_num
            self._update_page_label_continuous()

    def _update_page_label_continuous(self):
        """연속 모드에서 현재 보이는 페이지 번호를 라벨에 표시한다."""
        self._page_label.config(
            text=f"페이지 {self.current_page + 1} / {len(self.doc)}"
        )

    def _get_page_at_y(self, canvas_y: int) -> tuple[int, int]:
        """캔버스 y 좌표에 해당하는 (페이지 번호, 페이지 y 시작) 를 반환한다."""
        if not self._page_y_offsets:
            return self.current_page, 0
        page_num = 0
        page_y0 = 0
        for i, offset in enumerate(self._page_y_offsets):
            if offset <= canvas_y:
                page_num = i
                page_y0 = offset
            else:
                break
        return page_num, page_y0

    def _build_continuous_region(
        self, rx0: int, ry0: int, rx1: int, ry1: int
    ) -> tuple[Image.Image, int, bool]:
        """연속 스크롤 모드에서 드래그 영역을 합성 PIL 이미지로 반환한다.

        선택 영역이 여러 페이지에 걸칠 때 각 페이지의 해당 부분을 세로로 이어붙인다.

        Returns:
            (composite, primary_page_num, is_multi_page)
            - composite:         선택 영역 전체를 담은 PIL 이미지
            - primary_page_num:  드래그가 시작된 페이지 번호
            - is_multi_page:     2개 이상의 페이지에 걸쳐 있는지 여부
        """
        crops: list[Image.Image] = []
        primary_page = None

        for i, (page_img, y_offset) in enumerate(
            zip(self._pil_pages, self._page_y_offsets)
        ):
            page_top = y_offset
            page_bot = y_offset + page_img.height

            # 선택 영역과 이 페이지가 겹치지 않으면 건너뜀
            if ry1 <= page_top or ry0 >= page_bot:
                continue

            if primary_page is None:
                primary_page = i

            crop_y0 = max(0, ry0 - page_top)
            crop_y1 = min(page_img.height, ry1 - page_top)
            w = page_img.width
            crops.append(page_img.crop((max(0, rx0), crop_y0, min(w, rx1), crop_y1)))

        if not crops:
            # 폴백: 현재 페이지 그대로
            page_num, page_y0 = self._get_page_at_y(ry0)
            pil = self._pil_pages[page_num] if page_num < len(self._pil_pages) else self._pil_img
            if pil is None:
                return Image.new("RGB", (1, 1)), self.current_page, False
            w, h = pil.size
            adj_y0 = max(0, ry0 - page_y0)
            adj_y1 = min(h, ry1 - page_y0)
            return pil.crop((max(0, rx0), adj_y0, min(w, rx1), adj_y1)), page_num, False

        if len(crops) == 1:
            return crops[0], primary_page, False

        # 여러 페이지 crop을 흰 배경 위에 세로로 합성
        total_h = sum(c.height for c in crops)
        max_w = max(c.width for c in crops)
        composite = Image.new("RGB", (max_w, total_h), color=(255, 255, 255))
        y = 0
        for crop in crops:
            composite.paste(crop, (0, y))
            y += crop.height
        return composite, primary_page, True

    def _prev_page(self):
        if self._continuous_scroll:
            if self.current_page > 0:
                self._scroll_to_page(self.current_page - 1)
            return
        if self.current_page > 0:
            self.current_page -= 1
            self._render_page()

    def _next_page(self):
        if self._continuous_scroll:
            if self.current_page < len(self.doc) - 1:
                self._scroll_to_page(self.current_page + 1)
            return
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self._render_page()

    def _zoom_in(self):
        self.zoom = min(self.zoom + 0.25, 4.0)
        self._render_page()

    def _zoom_out(self):
        self.zoom = max(self.zoom - 0.25, 0.5)
        self._render_page()

    # ------------------------------------------------------- drag selection --

    def _canvas_xy(self, event):
        """Convert event coords to canvas (scrolled) coords."""
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _at_canvas_bottom(self) -> bool:
        """True when the canvas scroll position is at or near the very bottom."""
        top, bottom = self.canvas.yview()
        return bottom >= 0.999

    def _at_canvas_top(self) -> bool:
        """True when the canvas scroll position is at or near the very top."""
        top, _bottom = self.canvas.yview()
        return top <= 0.001

    def _on_mousewheel(self, event):
        """스크롤: 연속 모드는 끝없이 스크롤, 단일 모드는 페이지 끝에서 페이지 이동."""
        if hasattr(event, "delta") and event.delta != 0:
            going_down = event.delta < 0
        else:
            going_down = event.num == 5  # Button-5 is scroll-down on Linux

        if self._continuous_scroll:
            # 연속 모드: 페이지 전환 없이 캔버스를 계속 스크롤
            self.canvas.yview_scroll(3 if going_down else -3, "units")
            # 현재 보이는 페이지 번호 업데이트
            self._update_visible_page()
            return

        if going_down:
            if self._at_canvas_bottom():
                self._next_page()
            else:
                self.canvas.yview_scroll(3, "units")
        else:
            if self._at_canvas_top():
                self._prev_page()
            else:
                self.canvas.yview_scroll(-3, "units")

    def _update_visible_page(self):
        """연속 스크롤 모드에서 현재 화면 중앙에 보이는 페이지로 current_page를 갱신한다."""
        if not self._page_y_offsets:
            return
        sr = self.canvas.cget("scrollregion")
        if not sr:
            return
        parts = str(sr).split()
        if len(parts) < 4:
            return
        total_h = float(parts[3])
        top_frac, bot_frac = self.canvas.yview()
        mid_y = (top_frac + bot_frac) / 2 * total_h
        page_num, _ = self._get_page_at_y(int(mid_y))
        if page_num != self.current_page:
            self.current_page = page_num
            self._update_page_label_continuous()

    def _on_mouse_down(self, event):
        self.canvas.focus_set()  # grab keyboard focus on click
        self._drag_start = self._canvas_xy(event)
        if self._drag_rect:
            self.canvas.delete(self._drag_rect)
            self._drag_rect = None

    def _on_mouse_drag(self, event):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._canvas_xy(event)
        if self._drag_rect:
            self.canvas.coords(self._drag_rect, x0, y0, x1, y1)
        else:
            self._drag_rect = self.canvas.create_rectangle(
                x0, y0, x1, y1,
                outline="#FF6B6B", width=2, dash=(6, 3),
            )

    def _on_mouse_up(self, event):
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._canvas_xy(event)
        self._drag_start = None

        rx0, ry0 = int(min(x0, x1)), int(min(y0, y1))
        rx1, ry1 = int(max(x0, x1)), int(max(y0, y1))

        if (rx1 - rx0) < 5 or (ry1 - ry0) < 5:
            return  # too small – ignore

        # notify loading state on main thread before spawning worker
        self.event_generate("<<TranslationStarted>>")

        threading.Thread(
            target=self._do_translate,
            args=(rx0, ry0, rx1, ry1),
            daemon=True,
        ).start()

    def _do_translate(self, rx0: int, ry0: int, rx1: int, ry1: int):
        # 연속 스크롤 모드: 페이지 경계를 넘는 드래그를 합성 이미지로 처리
        if self._continuous_scroll and self._page_y_offsets:
            cropped, page_num, is_multi = self._build_continuous_region(rx0, ry0, rx1, ry1)
            # pdf_rect는 시작 페이지 기준 단일 페이지 범위로 계산
            _, page_y0 = self._get_page_at_y(ry0)
            page_img = self._pil_pages[page_num] if page_num < len(self._pil_pages) else None
            adj_ry0 = max(0, ry0 - page_y0)
            # 단일 페이지면 ry1도 보정, 멀티 페이지면 해당 페이지 끝까지만
            if page_img is not None:
                adj_ry1 = min(page_img.height, ry1 - page_y0)
            else:
                adj_ry1 = adj_ry0 + cropped.height
        else:
            page_num = self.current_page
            pil_img = self._pil_img
            is_multi = False
            if pil_img is None:
                self._translation_result = (None, "[오류] 렌더된 페이지가 없습니다")
                self.event_generate("<<TranslationReady>>")
                return
            w, h = pil_img.size
            cropped = pil_img.crop((
                max(0, rx0), max(0, ry0),
                min(w, rx1), min(h, ry1),
            ))
            adj_ry0, adj_ry1 = ry0, ry1

        if cropped is None or cropped.width < 1 or cropped.height < 1:
            self._translation_result = (None, "[오류] 렌더된 페이지가 없습니다")
            self.event_generate("<<TranslationReady>>")
            return

        if self._translation_mode == "markdown":
            from src.table_handler import detect_tables, render_table_image, crop_table_pct
            from src.translator import analyze_and_translate

            pdf_rect = fitz.Rect(
                rx0 / self.zoom, adj_ry0 / self.zoom,
                rx1 / self.zoom, adj_ry1 / self.zoom,
            )
            page = self.doc[page_num]

            if is_multi:
                # 멀티 페이지: 합성 이미지를 직접 AI에 전달 (고해상도 재렌더 불필요)
                high_res = cropped
            else:
                # 단일 페이지: 기존과 동일하게 150 DPI 재렌더
                high_res = render_table_image(page, pdf_rect, dpi=150)

            # AI에 high_res 전달: 고해상도 이미지로 좌표 추정 정확도 향상
            layout_info = analyze_and_translate(high_res)
            md       = layout_info["markdown"]
            layout   = layout_info["layout"]
            tbl_meta = layout_info["tables"]    # [{id, x_pct, y_pct, w_pct, h_pct}, ...]
            fig_meta = layout_info.get("figures", [])  # [{id, x_pct, y_pct, w_pct, h_pct}, ...]

            import re as _re
            tbl_markers = _re.findall(r'\[TABLE_\d+\]', md, _re.IGNORECASE)
            fig_markers = _re.findall(r'\[FIGURE_\d+\]', md, _re.IGNORECASE)

            has_tables  = bool(tbl_markers and layout_info["has_tables"])
            has_figures = bool(fig_markers and layout_info.get("has_figures", False))

            if has_tables:
                # [1순위] PyMuPDF 정밀 크롭 (단일 페이지에서만 의미있음)
                table_rects = [] if is_multi else detect_tables(page, pdf_rect)
                if table_rects and len(table_rects) >= len(tbl_markers):
                    table_images = [
                        render_table_image(page, r)
                        for r in table_rects[:len(tbl_markers)]
                    ]
                elif tbl_meta:
                    # [2순위] AI % 좌표로 개별 크롭
                    table_images = [
                        crop_table_pct(
                            high_res,
                            t["x_pct"], t["y_pct"],
                            t["w_pct"], t["h_pct"],
                        )
                        for t in tbl_meta[:len(tbl_markers)]
                    ]
                    if len(table_images) < len(tbl_markers):
                        table_images += [high_res] * (len(tbl_markers) - len(table_images))
                else:
                    table_images = [high_res] * len(tbl_markers)
            else:
                table_images = []

            if has_figures and fig_meta:
                # 그림은 PyMuPDF로 자동 감지 불가 → AI % 좌표로 크롭
                figure_images = [
                    crop_table_pct(
                        high_res,
                        f["x_pct"], f["y_pct"],
                        f["w_pct"], f["h_pct"],
                    )
                    for f in fig_meta[:len(fig_markers)]
                ]
                if len(figure_images) < len(fig_markers):
                    figure_images += [high_res] * (len(fig_markers) - len(figure_images))
                fig_layout = _detect_figure_layout(fig_meta[:len(fig_markers)])
            else:
                figure_images = []
                fig_layout = "single"

            if has_tables or has_figures:
                result = {
                    "type":          "table_aware",
                    "markdown":      md,
                    "table_images":  table_images,
                    "figure_images": figure_images,
                    "fig_layout":    fig_layout,
                    "layout":        layout,
                }
            else:
                # AI가 표·그림 없다고 판단 → 일반 마크다운으로 처리
                result = md
        else:
            result = translate_region(cropped)

        self._translation_result = (cropped, result)
        self.event_generate("<<TranslationReady>>")

    def set_translation_mode(self, mode: str):
        self._translation_mode = mode

    @property
    def last_translation(self) -> tuple:
        """Returns (PIL.Image | None, translated_str)."""
        return self._translation_result
