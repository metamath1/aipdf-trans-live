"""Right-side translation panel – embedded in main window."""

import threading
import tkinter as tk

import fitz  # PyMuPDF
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from PIL import Image, ImageTk


class TranslationPanel(ttk.Frame):
    """Integrated right panel: translation text / rendered PDF display."""

    def __init__(self, parent):
        super().__init__(parent, padding=(8, 8))
        self._current_markdown = ""
        self._pdf_photo_refs: list = []
        self._pdf_bytes: bytes | None = None      # last rendered PDF bytes
        self._pdf_scale_factor: float = 1.0       # zoom multiplier on top of fit-width
        self._build_ui()

    def _build_ui(self):
        # ---- header ----
        header = ttk.Frame(self)
        header.pack(fill=X, pady=(0, 4))

        self._backend_badge = ttk.Label(
            header,
            text="Claude",
            bootstyle="inverse-info",
            font=("맑은 고딕", 8, "bold"),
            padding=(4, 1),
        )
        self._backend_badge.pack(side=LEFT)

        self._status_label = ttk.Label(
            header,
            text="영역을 드래그하면 번역이 표시됩니다",
            bootstyle="secondary",
            font=("맑은 고딕", 8),
        )
        self._status_label.pack(side=LEFT, padx=(6, 0))

        ttk.Separator(self, orient=HORIZONTAL).pack(fill=X, pady=(4, 6))

        # ---- translation text (text mode / loading / error) ----
        self._trans_box = ScrolledText(
            self,
            wrap=WORD,
            autohide=True,
            height=16,
            font=("맑은 고딕", 11),
        )
        self._trans_box.pack(fill=BOTH, expand=YES, pady=(2, 0))
        self._trans_box.text.config(state=DISABLED)

        # ---- PDF canvas (markdown mode rendered result) ----
        self._pdf_frame = ttk.Frame(self)

        # Zoom toolbar inside the PDF frame
        _zoom_bar = ttk.Frame(self._pdf_frame)
        ttk.Button(
            _zoom_bar, text="확대 +", command=self._pdf_zoom_in,
            bootstyle="outline-secondary", width=6,
        ).pack(side=LEFT, padx=2, pady=2)
        ttk.Button(
            _zoom_bar, text="축소 −", command=self._pdf_zoom_out,
            bootstyle="outline-secondary", width=6,
        ).pack(side=LEFT, padx=2, pady=2)
        ttk.Button(
            _zoom_bar, text="폭 맞춤", command=self._pdf_zoom_fit,
            bootstyle="outline-secondary", width=6,
        ).pack(side=LEFT, padx=2, pady=2)
        _zoom_bar.pack(side=TOP, fill=X)

        # Canvas + vertical scrollbar fill remaining space
        self._pdf_canvas = tk.Canvas(
            self._pdf_frame, bg="#ffffff", highlightthickness=0
        )
        self._pdf_vbar = ttk.Scrollbar(
            self._pdf_frame, orient=VERTICAL, command=self._pdf_canvas.yview,
            bootstyle="round-secondary",
        )
        self._pdf_canvas.configure(yscrollcommand=self._pdf_vbar.set)
        self._pdf_vbar.pack(side=RIGHT, fill=Y)
        self._pdf_canvas.pack(side=LEFT, fill=BOTH, expand=YES)
        self._pdf_canvas.bind("<MouseWheel>", self._on_pdf_scroll)
        self._pdf_canvas.bind("<Configure>", self._on_pdf_canvas_configure)
        # _pdf_frame starts hidden; shown after PDF render

        # ---- browser button ----
        self._open_btn = ttk.Button(
            self,
            text="🌐  브라우저에서 열기",
            command=self._open_browser,
            bootstyle="info-outline",
            state=DISABLED,
        )
        self._open_btn.pack(fill=X, pady=(6, 0))

    # ---------------------------------------------------------------- public --

    def show_result(self, translated: str):
        self._show_text_view()
        self._set_text(translated)
        self._status_label.config(text="완료")
        self._open_btn.config(state=DISABLED)

    def show_markdown_result(self, markdown: str):
        self._current_markdown = markdown
        self._show_text_view()
        self._set_text("PDF 생성 중…")
        self._status_label.config(text="렌더링 중…")
        self._open_btn.config(state=DISABLED)
        threading.Thread(target=self._generate_pdf, args=(markdown,), daemon=True).start()

    def show_table_aware_result(self, markdown: str, table_images: list):
        """표 이미지 보존 모드: [TABLE_N] 플레이스홀더 마크다운 + 실제 표 이미지로 PDF 생성."""
        self._current_markdown = markdown
        self._table_images = table_images
        self._pdf_bytes = None
        self._show_text_view()
        self._set_text("PDF 생성 중… (표 이미지 삽입)")
        self._status_label.config(text="렌더링 중…")
        self._open_btn.config(state=DISABLED)
        threading.Thread(
            target=self._generate_pdf_with_tables,
            args=(markdown, table_images),
            daemon=True,
        ).start()

    def show_error(self, message: str):
        self._show_text_view()
        self._set_text(message)
        self._status_label.config(text="오류 발생")
        self._open_btn.config(state=DISABLED)

    def show_loading(self):
        self._show_text_view()
        self._set_text("번역 중…")
        self._status_label.config(text="번역 중…")
        self._open_btn.config(state=DISABLED)

    def set_status(self, text: str):
        self._status_label.config(text=text)

    def update_backend_label(self, backend: str):
        if backend == "gemini":
            self._backend_badge.config(text="Gemini", bootstyle="inverse-success")
        else:
            self._backend_badge.config(text="Claude", bootstyle="inverse-info")

    # --------------------------------------------------------------- private --

    def _generate_pdf_with_tables(self, markdown: str, table_images: list):
        """표 이미지 포함 PDF 생성 (백그라운드 스레드)."""
        try:
            from src.renderer import markdown_with_tables_to_pdf_bytes
            pdf_bytes = markdown_with_tables_to_pdf_bytes(markdown, table_images)
            self.after(0, self._on_pdf_ready, pdf_bytes)
        except Exception as exc:
            self.after(0, self._on_pdf_error, markdown, str(exc))

    def _generate_pdf(self, markdown: str):
        try:
            from src.renderer import markdown_to_pdf_bytes
            pdf_bytes = markdown_to_pdf_bytes(markdown)
            self.after(0, self._on_pdf_ready, pdf_bytes)
        except Exception as exc:
            # Fallback: show raw markdown text
            self.after(0, self._on_pdf_error, markdown, str(exc))

    def _on_pdf_ready(self, pdf_bytes: bytes):
        self._pdf_bytes = pdf_bytes          # store for zoom / resize re-render
        self._pdf_scale_factor = 1.0         # reset zoom for each new translation
        self._draw_pdf_pages(pdf_bytes)
        self._show_pdf_view()
        self._status_label.config(text="번역 완료")
        self._open_btn.config(state=NORMAL)

    def _draw_pdf_pages(self, pdf_bytes: bytes):
        """Render PDF bytes → images → canvas (respects current scale factor)."""
        images = self._render_pdf_pages(pdf_bytes)
        if not images:
            self._status_label.config(text="PDF 렌더링 실패")
            return

        self._pdf_canvas.delete("all")
        self._pdf_photo_refs = []
        y = 0
        for img in images:
            photo = ImageTk.PhotoImage(img)
            self._pdf_photo_refs.append(photo)
            self._pdf_canvas.create_image(0, y, anchor=NW, image=photo)
            y += img.height + 6

        total_w = images[0].width
        self._pdf_canvas.configure(scrollregion=(0, 0, total_w, y))

    def _on_pdf_error(self, markdown: str, error: str):
        self._show_text_view()
        self._set_text(markdown)
        self._status_label.config(text=f"PDF 생성 실패 (브라우저 버튼 사용): {error[:60]}")
        self._open_btn.config(state=NORMAL)

    def _render_pdf_pages(self, pdf_bytes: bytes) -> list:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        panel_w = max(300, self.winfo_width() - 24)
        images = []
        for page in doc:
            scale = (panel_w / page.rect.width) * self._pdf_scale_factor
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        doc.close()
        return images

    def _show_text_view(self):
        self._pdf_frame.pack_forget()
        self._trans_box.pack(fill=BOTH, expand=YES, pady=(2, 0))

    def _show_pdf_view(self):
        self._trans_box.pack_forget()
        self._pdf_frame.pack(fill=BOTH, expand=YES, pady=(2, 0))

    def _set_text(self, text: str):
        self._trans_box.text.config(state=NORMAL)
        self._trans_box.text.delete("1.0", END)
        self._trans_box.text.insert(END, text)
        self._trans_box.text.config(state=DISABLED)

    def _open_browser(self):
        from src.renderer import open_in_browser
        if self._current_markdown:
            open_in_browser(self._current_markdown)

    def _on_pdf_scroll(self, event):
        self._pdf_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # --------------------------------------------------------- PDF zoom / resize --

    def _pdf_zoom_in(self):
        self._pdf_scale_factor = min(self._pdf_scale_factor + 0.25, 4.0)
        self._rerender_pdf()

    def _pdf_zoom_out(self):
        self._pdf_scale_factor = max(self._pdf_scale_factor - 0.25, 0.25)
        self._rerender_pdf()

    def _pdf_zoom_fit(self):
        self._pdf_scale_factor = 1.0
        self._rerender_pdf()

    def _rerender_pdf(self):
        """Re-draw PDF pages using current scale factor."""
        if self._pdf_bytes:
            self._draw_pdf_pages(self._pdf_bytes)

    def _on_pdf_canvas_configure(self, event):
        """Re-fit PDF width when the right panel is resized (debounced)."""
        if self._pdf_bytes is None:
            return
        if hasattr(self, "_pdf_resize_job"):
            self.after_cancel(self._pdf_resize_job)
        self._pdf_resize_job = self.after(200, self._rerender_pdf)
