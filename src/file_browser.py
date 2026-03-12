"""Simple PDF file-browser sidebar."""

import os
import tkinter as tk
from tkinter import ttk
from pathlib import Path


class FileBrowser(tk.Frame):
    """Left-panel file list that shows PDFs in a directory."""

    def __init__(self, parent, pdf_dir: str | Path, on_select):
        super().__init__(parent, width=200)
        self.pdf_dir = Path(pdf_dir)
        self.on_select = on_select
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        header = tk.Frame(self)
        header.pack(fill=tk.X)
        tk.Label(header, text="PDF 목록", font=("맑은 고딕", 10, "bold"),
                 anchor="w").pack(side=tk.LEFT, padx=6, pady=4)
        tk.Button(header, text="↺", command=self.refresh,
                  relief=tk.FLAT, cursor="hand2").pack(side=tk.RIGHT, padx=4)

        self._listbox = tk.Listbox(self, selectmode=tk.SINGLE,
                                   font=("맑은 고딕", 9), activestyle="none")
        scroll = ttk.Scrollbar(self, orient=tk.VERTICAL,
                               command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scroll.set)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._listbox.bind("<<ListboxSelect>>", self._on_select)

    def refresh(self):
        self._listbox.delete(0, tk.END)
        self._files = sorted(self.pdf_dir.glob("*.pdf"))
        for f in self._files:
            self._listbox.insert(tk.END, f.name)

    def _on_select(self, _event):
        sel = self._listbox.curselection()
        if sel:
            self.on_select(str(self._files[sel[0]]))
