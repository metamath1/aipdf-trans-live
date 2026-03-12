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
    translate_to_markdown_table_aware,
)


class PDFViewer(ttk.Frame):
    """Scrollable PDF viewer with drag-to-select, image capture, and translation."""

    ZOOM = 1.5  # render scale

    def __init__(self, parent, pdf_path: str):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
        self.current_page = 0
        self.zoom = self.ZOOM

        self._pil_img: Image.Image | None = None  # raw PIL image of current page
        self._drag_start = None
        self._drag_rect = None
        self._translation_result: tuple = (None, "")
        self._translation_mode = "markdown"

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
        page = self.doc[self.current_page]
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        self._pil_img = img  # store raw PIL image for cropping (canvas px == image px)
        self._photo = ImageTk.PhotoImage(img)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=NW, image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, pix.width, pix.height))

        self._page_label.config(
            text=f"페이지 {self.current_page + 1} / {len(self.doc)}"
        )

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._render_page()

    def _next_page(self):
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
        """Scroll within the page; flip to next/prev page when at the edge."""
        # Determine scroll direction
        if hasattr(event, "delta") and event.delta != 0:
            going_down = event.delta < 0
        else:
            going_down = event.num == 5  # Button-5 is scroll-down on Linux

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
        if self._pil_img is None:
            self._translation_result = (None, "[오류] 렌더된 페이지가 없습니다")
            self.event_generate("<<TranslationReady>>")
            return

        w, h = self._pil_img.size
        cropped = self._pil_img.crop((
            max(0, rx0), max(0, ry0),
            min(w, rx1), min(h, ry1),
        ))

        if self._translation_mode == "markdown":
            # AI가 표 유무를 판단 (TABLE_AWARE_PROMPT: 표 위치에 [TABLE_N] 마커 삽입)
            # PyMuPDF find_tables()보다 AI Vision이 복잡한 학술 표 감지에 더 신뢰성 높음
            md = translate_to_markdown_table_aware(cropped, max_tables=5)

            import re as _re
            markers = _re.findall(r'\[TABLE_\d+\]', md, _re.IGNORECASE)

            if markers:
                # AI가 표를 감지함 → 원본 PDF에서 표 이미지 추출
                pdf_rect = fitz.Rect(
                    rx0 / self.zoom, ry0 / self.zoom,
                    rx1 / self.zoom, ry1 / self.zoom,
                )
                page = self.doc[self.current_page]

                from src.table_handler import detect_tables, render_table_image
                table_rects = detect_tables(page, pdf_rect)

                if table_rects and len(table_rects) >= len(markers):
                    # PyMuPDF가 정확한 표 영역을 찾은 경우 – 정밀 크롭
                    table_images = [
                        render_table_image(page, r)
                        for r in table_rects[: len(markers)]
                    ]
                else:
                    # PyMuPDF 감지 실패 또는 수 불일치
                    # → 전체 선택 영역을 고해상도로 렌더링해 모든 마커에 공유
                    full_img = render_table_image(page, pdf_rect)
                    if table_rects:
                        # 일부는 정밀 크롭, 나머지는 전체 이미지로 보완
                        table_images = [render_table_image(page, r) for r in table_rects]
                        table_images += [full_img] * (len(markers) - len(table_rects))
                    else:
                        table_images = [full_img] * len(markers)

                result = {
                    "type": "table_aware",
                    "markdown": md,
                    "table_images": table_images,
                }
            else:
                # AI가 표 없다고 판단 → 일반 마크다운으로 처리
                # (TABLE_AWARE_PROMPT도 표 없으면 MARKDOWN_PROMPT와 동일한 출력)
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
