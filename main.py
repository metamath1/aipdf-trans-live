"""aipdf-trans-live – PDF drag-to-translate desktop app.

Usage:
    uv run main.py [pdf_dir]

    pdf_dir: path to folder containing PDFs (default: ./pdfs)
"""

import os
import sys
from pathlib import Path
from tkinter import filedialog
from dotenv import load_dotenv

load_dotenv()

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from src.pdf_viewer import PDFViewer
from src.translation_panel import TranslationPanel
from src import translator


class App(ttk.Window):
    def __init__(self, pdf_dir: Path):
        super().__init__(title="AI PDF 번역기", themename="darkly", size=(1400, 860))
        self.minsize(900, 600)

        self._pdf_dir = pdf_dir
        self._viewer: PDFViewer | None = None

        self._build_toolbar()
        self._build_layout()

        # Set initial sash position after window renders
        self.after(50, self._set_initial_sash)

    # --------------------------------------------------------------- toolbar --

    def _build_toolbar(self):
        toolbar = ttk.Frame(self, padding=(4, 4))
        toolbar.pack(side=TOP, fill=X)

        ttk.Label(toolbar, text="번역 엔진:", font=("맑은 고딕", 9)).pack(
            side=LEFT, padx=(8, 2)
        )

        self._backend_var = ttk.StringVar(
            value=os.environ.get("TRANSLATOR_BACKEND", "claude").lower()
        )
        backend_combo = ttk.Combobox(
            toolbar,
            textvariable=self._backend_var,
            values=["claude", "gemini"],
            state="readonly",
            width=9,
            bootstyle="secondary",
        )
        backend_combo.pack(side=LEFT, padx=(0, 8))
        backend_combo.bind("<<ComboboxSelected>>", self._on_backend_change)

        self._backend_status = ttk.Label(
            toolbar,
            text=self._backend_label(),
            bootstyle="secondary",
            font=("맑은 고딕", 8),
        )
        self._backend_status.pack(side=LEFT, padx=(0, 12))

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=4)

        ttk.Label(toolbar, text="번역 모드:", font=("맑은 고딕", 9)).pack(
            side=LEFT, padx=(4, 2)
        )

        self._mode_var = ttk.StringVar(value="markdown")
        ttk.Radiobutton(
            toolbar, text="Markdown", variable=self._mode_var,
            value="markdown", bootstyle="toolbutton-info",
        ).pack(side=LEFT, padx=1)
        ttk.Radiobutton(
            toolbar, text="텍스트", variable=self._mode_var,
            value="text", bootstyle="toolbutton-secondary",
        ).pack(side=LEFT, padx=(1, 8))

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=4)

        ttk.Button(
            toolbar,
            text="  PDF 열기  ",
            command=self._open_file_dialog,
            bootstyle="outline-light",
        ).pack(side=LEFT, padx=(4, 2))

    # --------------------------------------------------------------- layout --

    def _build_layout(self):
        self._paned = ttk.Panedwindow(self, orient=HORIZONTAL)
        self._paned.pack(fill=BOTH, expand=YES)

        # Left pane: PDF viewer
        self._left = ttk.Frame(self._paned)
        self._paned.add(self._left, weight=1)

        self._placeholder = ttk.Label(
            self._left,
            text="PDF 파일을 열어 주세요\n\n[PDF 열기] 버튼을 클릭하세요",
            anchor=CENTER,
            bootstyle="secondary",
            font=("맑은 고딕", 13),
            justify=CENTER,
        )
        self._placeholder.place(relx=0.5, rely=0.5, anchor=CENTER)

        # Right pane: Translation panel
        self._trans_panel = TranslationPanel(self._paned)
        self._paned.add(self._trans_panel, weight=1)

        # Sync badge with current backend on startup
        self._trans_panel.update_backend_label(self._backend_var.get())

        # Keep sash at 50% on window resize (e.g. maximise)
        self.bind("<Configure>", self._on_window_configure)

    def _set_initial_sash(self):
        """Force 50% / 50% split."""
        total = self.winfo_width()
        if total > 1:
            self._paned.sashpos(0, int(total * 0.50))

    def _on_window_configure(self, event):
        """Debounced handler: reset sash to 50% after any window resize."""
        if event.widget is not self:
            return
        if hasattr(self, "_sash_job"):
            self.after_cancel(self._sash_job)
        self._sash_job = self.after(80, self._set_initial_sash)

    # ------------------------------------------------------------ callbacks --

    def _backend_label(self) -> str:
        backend = self._backend_var.get()
        if backend == "gemini":
            key = os.environ.get("GEMINI_API_KEY", "")
            model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
        else:
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
        status = "✓" if key else "✗ API 키 없음"
        return f"{model}  [{status}]"

    def _on_backend_change(self, _event):
        selected = self._backend_var.get()
        os.environ["TRANSLATOR_BACKEND"] = selected
        translator.reset_clients()
        self._backend_status.config(text=self._backend_label())
        self._trans_panel.update_backend_label(selected)

    def _open_file_dialog(self):
        path = filedialog.askopenfilename(
            title="PDF 파일 선택",
            initialdir=str(self._pdf_dir),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self._open_pdf(path)

    def _open_pdf(self, path: str):
        if self._viewer:
            self._viewer.unbind("<<TranslationStarted>>")
            self._viewer.unbind("<<TranslationReady>>")
            self._viewer.destroy()
            self._viewer = None

        self._placeholder.place_forget()
        self._viewer = PDFViewer(self._left, path)
        self._viewer.pack(fill=BOTH, expand=YES)
        self._viewer.bind("<<TranslationStarted>>", self._on_translation_started)
        self._viewer.bind("<<TranslationReady>>", self._on_translation_ready)

        self.title(f"AI PDF 번역기 – {Path(path).name}")
        self._trans_panel.set_status("PDF 열림 – 영역을 드래그하세요")

    def _on_translation_started(self, _event):
        if self._viewer:
            self._viewer.set_translation_mode(self._mode_var.get())
        self._trans_panel.show_loading()

    def _on_translation_ready(self, _event):
        if self._viewer is None:
            return
        _img, result = self._viewer.last_translation
        if _img is None:
            self._trans_panel.show_error(result)
        elif isinstance(result, dict) and result.get("type") == "table_aware":
            # 표가 감지된 경우: 원본 표 이미지 + 번역된 텍스트
            self._trans_panel.show_table_aware_result(
                result["markdown"], result["table_images"]
            )
        elif self._mode_var.get() == "markdown":
            self._trans_panel.show_markdown_result(result)
        else:
            self._trans_panel.show_result(result)


def main():
    pdf_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pdfs")
    pdf_dir.mkdir(exist_ok=True)

    app = App(pdf_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
