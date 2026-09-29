"""
Unlimited-OCR Studio: a desktop GUI for converting PDFs/images to Markdown with Baidu Unlimited-OCR.

    python app.py            (or double-click run_gui.bat)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import ocr_engine  # noqa: F401  (sets up stdout for pythonw before torch/transformers import)
from ocr_engine import (DEFAULT_MODEL, IMAGE_EXTS, MODES, Cancelled, DocResult, OCREngine, OCRSettings,
                        PageResult, count_pages, save_document)
from md_render import render_html

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QStyle, QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget)

try:
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except ImportError:  # fall back to Qt's built-in markdown (no math rendering)
    from PySide6.QtWidgets import QTextBrowser
    HAVE_WEBENGINE = False

APP_NAME = "Unlimited-OCR Studio"
SUPPORTED = "Documents (*.pdf " + " ".join(f"*{e}" for e in sorted(IMAGE_EXTS)) + ")"
COL_FILE, COL_PAGES, COL_RANGE, COL_STATUS = range(4)
STATUS_COLORS = {"Queued": None, "Running": "#1f6feb", "Done": "#1a7f37", "Error": "#cf222e",
                 "Stopped": "#9a6700"}


class FileJob:
    def __init__(self, path: Path):
        self.path = path
        self.n_pages = count_pages(path)
        self.status = "Queued"
        self.work_dir: Path | None = None
        self.pages: list[PageResult] = []
        self.doc: DocResult | None = None
        self.edited_md: str | None = None       # user edits in the Markdown tab
        self.saved = False
        self.error = ""

    def current_doc(self, settings: OCRSettings) -> DocResult | None:
        if self.doc:
            return self.doc
        if self.work_dir and self.pages:
            return DocResult(self.path, self.work_dir, list(self.pages), settings)
        return None

    def markdown(self, settings: OCRSettings) -> str:
        if self.edited_md is not None:
            return self.edited_md
        doc = self.current_doc(settings)
        return doc.markdown() if doc else ""


class Worker(QThread):
    status = Signal(str)
    model_ready = Signal(str)
    file_started = Signal(int, int)                    # row, pages to do
    page_started = Signal(int, int, int, int, str)     # row, done, total, page_index, png
    token = Signal(str)
    page_done = Signal(int, object, int, int)          # row, PageResult, done, total
    file_done = Signal(int, object)                    # row, DocResult
    file_failed = Signal(int, str)
    file_stopped = Signal(int)

    def __init__(self, engine: OCREngine, jobs: list[tuple[int, Path, str, Path]], settings: OCRSettings):
        super().__init__()
        self.engine, self.jobs, self.settings = engine, jobs, settings
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True
        self.engine.cancel()

    def run(self):
        try:
            if not self.engine.loaded:
                self.engine.load(self.status.emit)
                self.model_ready.emit(self.engine.model_name)
        except Exception as e:  # noqa: BLE001
            for row, *_ in self.jobs:
                self.file_failed.emit(row, f"Model failed to load: {e}")
            self.status.emit(f"Model failed to load: {e}")
            return
        for row, path, spec, work in self.jobs:
            if self.stop_requested:
                self.file_stopped.emit(row)
                continue
            try:
                from ocr_engine import parse_page_range
                self.file_started.emit(row, len(parse_page_range(spec, count_pages(path))))
                doc = self.engine.convert(
                    path, self.settings, spec, work,
                    on_status=self.status.emit,
                    on_page_start=lambda d, t, pi, png, r=row: self.page_started.emit(r, d, t, pi, str(png)),
                    on_token=self.token.emit,
                    on_page_done=lambda p, d, t, r=row: self.page_done.emit(r, p, d, t))
                self.file_done.emit(row, doc)
            except Cancelled:
                self.file_stopped.emit(row)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "out of memory" in msg.lower():
                    msg += "\n\nTip: close other GPU apps, use 'base' mode, or lower 'Max tokens / page'."
                traceback.print_exc()
                self.file_failed.emit(row, msg)
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass


class PageView(QScrollArea):
    """Shows a rendered page image, scaled to the available width."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.label = QLabel("Rendered page appears here")
        self.label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.label.setStyleSheet("color: gray; padding: 20px;")
        self.setWidget(self.label)
        self.pix: QPixmap | None = None

    def show_image(self, path: str | None):
        if not path or not Path(path).exists():
            self.pix = None
            self.label.setPixmap(QPixmap())
            self.label.setText("Rendered page appears here")
            return
        self.pix = QPixmap(path)
        self._rescale()

    def _rescale(self):
        if self.pix and not self.pix.isNull():
            w = max(200, self.viewport().width() - 8)
            self.label.setPixmap(self.pix.scaledToWidth(w, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.qs = QSettings("UnlimitedOCR", "Studio")
        self.tmp_root = Path(tempfile.mkdtemp(prefix="uocr_studio_"))
        self.engine: OCREngine | None = None
        self.worker: Worker | None = None
        self.jobs: list[FileJob] = []
        self.shown_row = -1
        self.page_images: list[str] = []
        self.run_total = self.run_done = 0
        self.run_started = 0.0
        self.page_t0 = 0.0
        self.page_tokens = 0
        self._loading_editor = False

        self._build_ui()
        self._load_prefs()
        self._update_actions()

        self.preview_timer = QTimer(self, singleShot=True, interval=700, timeout=self.refresh_preview)

    # ------------------------------------------------------------------ UI construction
    def _build_ui(self):
        st = self.style()
        tb = QToolBar("Main", movable=False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def act(text, icon, slot, shortcut=None, tip=None):
            a = QAction(st.standardIcon(icon), text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        self.a_add = act("Add files", QStyle.SP_DialogOpenButton, self.add_files_dialog, "Ctrl+O")
        self.a_remove = act("Remove", QStyle.SP_TrashIcon, self.remove_selected, "Del")
        tb.addSeparator()
        self.a_start = act("Start", QStyle.SP_MediaPlay, self.start, "F5", "Convert all queued files")
        self.a_stop = act("Stop", QStyle.SP_MediaStop, self.stop, "Esc")
        tb.addSeparator()
        self.a_save = act("Save as…", QStyle.SP_DialogSaveButton, self.save_as, "Ctrl+S")
        self.a_save_all = act("Save all…", QStyle.SP_DriveHDIcon, self.save_all, "Ctrl+Shift+S")
        self.a_copy = act("Copy Markdown", QStyle.SP_FileDialogDetailedView, self.copy_markdown)

        # --- left: file table + settings
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Pages", "Range", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_FILE, QHeaderView.Stretch)
        for c in (COL_PAGES, COL_RANGE, COL_STATUS):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.on_selection)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.setToolTip("Drop PDFs or images here. Edit 'Range' to convert only some pages, e.g. 1-3,7,10-")

        box = QGroupBox("Settings")
        form = QFormLayout(box)
        self.mode = QComboBox()
        for key, (desc, *_rest) in MODES.items():
            self.mode.addItem(f"{key} – {desc}", key)
        self.dpi = QSpinBox(minimum=72, maximum=600, singleStep=50, value=300, suffix=" dpi")
        self.max_tokens = QSpinBox(minimum=512, maximum=30000, singleStep=1024, value=8192)
        self.chunk = QSpinBox(minimum=2, maximum=40, value=6)
        self.dollar = QCheckBox("Use $…$ / $$…$$ for math")
        self.markers = QCheckBox("Insert page markers")
        self.figures = QCheckBox("Extract figures as images")
        self.headings = QCheckBox("Turn detected titles into # headings")
        self.drop_headers = QCheckBox("Remove page headers/footers/numbers")
        self.autosave = QCheckBox("Auto-save each finished file")
        self.out_dir = QLineEdit(placeholderText="next to the source file")
        b_out = QPushButton("…", maximumWidth=30, clicked=self.pick_out_dir)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_dir)
        out_row.addWidget(b_out)
        self.fmt = QComboBox()
        self.fmt.addItems(["md", "txt", "html"])
        self.model_name = QLineEdit(DEFAULT_MODEL)
        self.model_name.setToolTip("Hugging Face repo id or a local folder with the downloaded model")

        self.mode.setToolTip("gundam: tiles each page for maximum accuracy (recommended).\n"
                             "base: one view per page, a bit faster.\n"
                             "multi: several pages per pass so the model sees cross-page context "
                             "(uses more VRAM).")
        self.dpi.setToolTip("Rendering resolution for PDF pages (300 recommended by Baidu)")
        self.max_tokens.setToolTip("Upper limit of generated tokens per page; stops runaway output")
        self.dollar.setToolTip(r"Convert \( \) and \[ \] to $ and $$ (Obsidian, Typora, GitHub, Pandoc)")
        form.addRow("Mode", self.mode)
        form.addRow("Render DPI", self.dpi)
        form.addRow("Max tokens / page", self.max_tokens)
        form.addRow("Pages / chunk (multi)", self.chunk)
        form.addRow(self.dollar)
        form.addRow(self.markers)
        form.addRow(self.figures)
        form.addRow(self.headings)
        form.addRow(self.drop_headers)
        form.addRow(self.autosave)
        form.addRow("Output folder", out_row)
        form.addRow("Auto-save format", self.fmt)
        form.addRow("Model", self.model_name)
        self.mode.currentIndexChanged.connect(lambda: self.chunk.setEnabled(self.mode.currentData() == "multi"))

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 6)
        lv.addWidget(QLabel("<b>Queue</b> — drag &amp; drop PDFs or images"))
        lv.addWidget(self.table, 1)
        lv.addWidget(box)

        # --- middle: page image with navigation
        self.page_view = PageView()
        self.page_spin = QSpinBox(minimum=1, maximum=1, prefix="Page ")
        self.page_spin.valueChanged.connect(self.on_page_spin)
        self.page_info = QLabel("")
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 6, 0, 0)
        nav = QHBoxLayout()
        nav.addWidget(QLabel("<b>Source</b>"))
        nav.addStretch()
        nav.addWidget(self.page_info)
        nav.addWidget(self.page_spin)
        mv.addLayout(nav)
        mv.addWidget(self.page_view, 1)

        # --- right: output tabs
        mono = QFont("Consolas", 10)
        self.live = QPlainTextEdit(readOnly=True)
        self.live.setFont(mono)
        self.live.setPlaceholderText("Model output streams here while a page is being read…")
        self.editor = QPlainTextEdit()
        self.editor.setFont(mono)
        self.editor.setPlaceholderText("Markdown of the selected file. You can edit it before saving.")
        self.editor.textChanged.connect(self.on_editor_changed)
        if HAVE_WEBENGINE:
            self.preview = QWebEngineView()
            s = self.preview.settings()
            s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        else:
            self.preview = QTextBrowser()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.preview, "Preview")
        self.tabs.addTab(self.editor, "Markdown")
        self.tabs.addTab(self.live, "Live output")
        self.tabs.currentChanged.connect(lambda i: self.refresh_preview() if i == 0 else None)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(self.tabs)
        split.setSizes([380, 480, 640])
        self.setCentralWidget(split)

        # --- status bar
        self.progress = QProgressBar(maximumWidth=320, textVisible=True)
        self.progress.setFormat("%v / %m pages")
        self.progress.setValue(0)
        self.speed = QLabel("")
        self.statusBar().addPermanentWidget(self.speed)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Add PDF files to begin. The model loads on first Start (GPU required).")

    # ------------------------------------------------------------------ preferences
    def _load_prefs(self):
        q = self.qs
        i = self.mode.findData(q.value("mode", "gundam"))
        self.mode.setCurrentIndex(max(0, i))
        self.dpi.setValue(int(q.value("dpi", 300)))
        self.max_tokens.setValue(int(q.value("max_tokens", 8192)))
        self.chunk.setValue(int(q.value("chunk", 6)))
        self.chunk.setEnabled(self.mode.currentData() == "multi")
        self.dollar.setChecked(q.value("dollar", "true") == "true")
        self.markers.setChecked(q.value("markers", "true") == "true")
        self.figures.setChecked(q.value("figures", "true") == "true")
        self.headings.setChecked(q.value("headings", "true") == "true")
        self.drop_headers.setChecked(q.value("drop_headers", "true") == "true")
        self.autosave.setChecked(q.value("autosave", "false") == "true")
        self.out_dir.setText(q.value("out_dir", ""))
        self.fmt.setCurrentText(q.value("fmt", "md"))
        self.model_name.setText(q.value("model", DEFAULT_MODEL))
        if q.value("geometry"):
            self.restoreGeometry(q.value("geometry"))

    def _save_prefs(self):
        q = self.qs
        b = lambda c: "true" if c.isChecked() else "false"  # noqa: E731
        q.setValue("mode", self.mode.currentData())
        q.setValue("dpi", self.dpi.value())
        q.setValue("max_tokens", self.max_tokens.value())
        q.setValue("chunk", self.chunk.value())
        q.setValue("dollar", b(self.dollar))
        q.setValue("markers", b(self.markers))
        q.setValue("figures", b(self.figures))
        q.setValue("headings", b(self.headings))
        q.setValue("drop_headers", b(self.drop_headers))
        q.setValue("autosave", b(self.autosave))
        q.setValue("out_dir", self.out_dir.text())
        q.setValue("fmt", self.fmt.currentText())
        q.setValue("model", self.model_name.text().strip() or DEFAULT_MODEL)
        q.setValue("geometry", self.saveGeometry())

    def settings(self) -> OCRSettings:
        return OCRSettings(mode=self.mode.currentData(), dpi=self.dpi.value(),
                           max_new_tokens=self.max_tokens.value(), pages_per_chunk=self.chunk.value(),
                           dollar_math=self.dollar.isChecked(), page_markers=self.markers.isChecked(),
                           extract_figures=self.figures.isChecked(), headings=self.headings.isChecked(),
                           drop_headers=self.drop_headers.isChecked())

    # ------------------------------------------------------------------ queue management
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls()])

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add documents", self.qs.value("last_in", ""),
                                                f"{SUPPORTED};;All files (*)")
        if files:
            self.qs.setValue("last_in", str(Path(files[0]).parent))
            self.add_files(files)

    def add_files(self, files):
        paths = []
        for f in files:
            p = Path(f)
            if p.is_dir():
                paths += sorted(x for x in p.iterdir() if x.suffix.lower() in IMAGE_EXTS | {".pdf"})
            elif p.suffix.lower() in IMAGE_EXTS | {".pdf"}:
                paths.append(p)
        known = {j.path.resolve() for j in self.jobs}
        for p in paths:
            if p.resolve() in known:
                continue
            try:
                job = FileJob(p)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, APP_NAME, f"Cannot open {p.name}:\n{e}")
                continue
            self.jobs.append(job)
            self._add_row(job)
        if self.table.currentRow() < 0 and self.jobs:
            self.table.selectRow(0)
        self._update_actions()

    def _add_row(self, job: FileJob):
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(job.path.name)
        it.setToolTip(str(job.path))
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, COL_FILE, it)
        it = QTableWidgetItem(str(job.n_pages))
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        it.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(r, COL_PAGES, it)
        it = QTableWidgetItem("")
        it.setToolTip("Pages to convert, e.g. 1-3,7,10-  (blank = all)")
        self.table.setItem(r, COL_RANGE, it)
        it = QTableWidgetItem(job.status)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, COL_STATUS, it)
        self.table.blockSignals(False)

    def set_status(self, row: int, status: str, tip: str = ""):
        job = self.jobs[row]
        job.status = status
        it = self.table.item(row, COL_STATUS)
        it.setText(status)
        it.setToolTip(tip)
        color = STATUS_COLORS.get(status.split()[0])
        it.setForeground(QColor(color) if color else self.palette().text().color())

    def remove_selected(self):
        if self.running():
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            job = self.jobs.pop(r)
            if job.work_dir:
                shutil.rmtree(job.work_dir, ignore_errors=True)
            self.table.removeRow(r)
        self.shown_row = -1
        self.on_selection()
        self._update_actions()

    def on_item_changed(self, item: QTableWidgetItem):
        if item.column() == COL_RANGE:
            from ocr_engine import parse_page_range
            job = self.jobs[item.row()]
            try:
                parse_page_range(item.text(), job.n_pages)
                item.setForeground(self.palette().text().color())
                item.setToolTip("Pages to convert, e.g. 1-3,7,10-  (blank = all)")
            except ValueError as e:
                item.setForeground(QColor("#cf222e"))
                item.setToolTip(f"Invalid range: {e}")

    # ------------------------------------------------------------------ run control
    def running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _update_actions(self):
        run = self.running()
        job = self.current_job()
        has_output = bool(job and (job.pages or job.edited_md))
        self.a_start.setEnabled(not run and bool(self.jobs))
        all_done = self.jobs and all(j.status.startswith("Done") for j in self.jobs)
        self.a_start.setText("Re-run selected" if all_done else "Start")
        self.a_stop.setEnabled(run)
        self.a_add.setEnabled(not run)
        self.a_remove.setEnabled(not run and bool(self.jobs))
        self.a_save.setEnabled(has_output)
        self.a_copy.setEnabled(has_output)
        self.a_save_all.setEnabled(any(j.doc for j in self.jobs))
        for w in (self.mode, self.dpi, self.max_tokens, self.dollar, self.markers, self.figures,
                  self.headings, self.drop_headers, self.model_name):
            w.setEnabled(not run)
        self.chunk.setEnabled(not run and self.mode.currentData() == "multi")

    def start(self):
        from ocr_engine import parse_page_range
        if self.running():
            return
        rows = [r for r, j in enumerate(self.jobs) if not j.status.startswith("Done")]
        if not rows and self.current_job():   # everything done: re-run the selected file
            job = self.current_job()
            if (job.edited_md is not None and not job.saved and QMessageBox.question(
                    self, APP_NAME, f"Re-convert {job.path.name}? Your unsaved edits will be lost.")
                    != QMessageBox.Yes):
                return
            rows = [self.table.currentRow()]
        todo = []
        for r in rows:
            job = self.jobs[r]
            spec = self.table.item(r, COL_RANGE).text()
            try:
                parse_page_range(spec, job.n_pages)
            except ValueError as e:
                QMessageBox.warning(self, APP_NAME, f"{job.path.name}: {e}")
                return
            if job.work_dir:
                shutil.rmtree(job.work_dir, ignore_errors=True)
            job.work_dir = Path(tempfile.mkdtemp(prefix=f"{r:03d}_", dir=self.tmp_root))
            job.pages, job.doc, job.edited_md, job.saved = [], None, None, False
            self.set_status(r, "Queued")
            todo.append((r, job.path, spec, job.work_dir))
        if not todo:
            return
        self._save_prefs()
        model = self.model_name.text().strip() or DEFAULT_MODEL
        if self.engine is None or self.engine.model_name != model:
            if self.engine is not None:
                self.engine.unload()
            self.engine = OCREngine(model)
        self.run_total = sum(len(parse_page_range(s, self.jobs[r].n_pages)) for r, _, s, _ in todo)
        self.run_done = 0
        self.progress.setMaximum(self.run_total)
        self.progress.setValue(0)
        self.run_started = time.time()
        self.worker = Worker(self.engine, todo, self.settings())
        w = self.worker
        w.status.connect(self.statusBar().showMessage)
        w.model_ready.connect(lambda m: self.speed.setText("model loaded"))
        w.file_started.connect(self.on_file_started)
        w.page_started.connect(self.on_page_started)
        w.token.connect(self.on_token)
        w.page_done.connect(self.on_page_done)
        w.file_done.connect(self.on_file_done)
        w.file_failed.connect(self.on_file_failed)
        w.file_stopped.connect(lambda r: self.set_status(r, "Stopped"))
        w.finished.connect(self.on_finished)
        if not self.engine.loaded:
            self.progress.setMaximum(0)   # busy indicator while the model loads
        w.start()
        self._update_actions()

    def stop(self):
        if self.running():
            self.worker.stop()
            self.statusBar().showMessage("Stopping after the current step…")

    # ------------------------------------------------------------------ worker callbacks
    def on_file_started(self, row: int, n: int):
        self.progress.setMaximum(self.run_total)
        self.set_status(row, f"Running 0/{n}")
        self.table.selectRow(row)
        self.show_job(row)

    def on_page_started(self, row, done, total, page_index, png):
        self.page_t0, self.page_tokens = time.time(), 0
        self.live.appendPlainText(f"\n──────── page {page_index + 1} ────────\n")
        if self.shown_row == row:
            self._refresh_page_list(row)
            idx = self._page_list_index(png)
            if idx >= 0:
                self.page_spin.setValue(idx + 1)
            self.page_view.show_image(png)

    def on_token(self, text: str):
        self.page_tokens += len(text)
        cur = self.live.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        cur.insertText(text)
        self.live.setTextCursor(cur)
        dt = time.time() - self.page_t0
        if dt > 1:
            self.speed.setText(f"reading page… {self.page_tokens} chars, {dt:.0f}s")

    def on_page_done(self, row, page: PageResult, done, total):
        job = self.jobs[row]
        job.pages.append(page)
        self.run_done += 1
        self.progress.setValue(self.run_done)
        self.set_status(row, f"Running {done}/{total}")
        elapsed = time.time() - self.run_started
        eta = elapsed / self.run_done * (self.run_total - self.run_done)
        self.speed.setText(f"last page {page.seconds:.0f}s, {page.tokens} tok"
                           f" ({page.tokens / max(page.seconds, 1e-3):.0f} tok/s) · ETA {fmt_time(eta)}")
        if self.shown_row == row:
            self._set_editor(job.markdown(self.settings()))
            self.preview_timer.start()

    def on_file_done(self, row, doc: DocResult):
        job = self.jobs[row]
        job.doc = doc
        job.pages = doc.pages
        self.set_status(row, "Done")
        if self.autosave.isChecked():
            out = Path(self.out_dir.text()) if self.out_dir.text().strip() else job.path.parent
            try:
                dest = save_document(doc, out / f"{job.path.stem}.{self.fmt.currentText()}")
                job.saved = True
                self.set_status(row, "Done ✓ saved", str(dest))
            except Exception as e:  # noqa: BLE001
                self.set_status(row, "Done (save failed)", str(e))
        if self.shown_row == row:
            self.show_job(row)
        self._update_actions()

    def on_file_failed(self, row, msg):
        self.set_status(row, "Error", msg)
        self.live.appendPlainText(f"\n[ERROR] {self.jobs[row].path.name}: {msg}\n")

    def on_finished(self):
        self.progress.setMaximum(max(1, self.run_total))
        elapsed = time.time() - self.run_started
        errors = [j for j in self.jobs if j.status == "Error"]
        msg = f"Finished {self.run_done} page(s) in {fmt_time(elapsed)}"
        if errors:
            msg += f" — {len(errors)} file(s) failed (hover the status for details)"
        self.statusBar().showMessage(msg)
        self.worker = None
        self._update_actions()
        if errors and len(errors) == len(self.jobs):
            QMessageBox.critical(self, APP_NAME, self.table.item(self.jobs.index(errors[0]), COL_STATUS).toolTip())

    # ------------------------------------------------------------------ display
    def current_job(self) -> FileJob | None:
        r = self.table.currentRow()
        return self.jobs[r] if 0 <= r < len(self.jobs) else None

    def on_selection(self):
        r = self.table.currentRow()
        if r != self.shown_row:
            self.show_job(r)
        self._update_actions()

    def show_job(self, row: int):
        self.shown_row = row
        if not (0 <= row < len(self.jobs)):
            self._set_editor("")
            self.page_view.show_image(None)
            self.refresh_preview()
            return
        job = self.jobs[row]
        self._refresh_page_list(row)
        if self.page_images:
            self.page_view.show_image(self.page_images[self.page_spin.value() - 1])
        else:
            self.page_view.show_image(None)
            self._show_pdf_page(job, 0)
        self._set_editor(job.markdown(self.settings()))
        self.refresh_preview()

    def _refresh_page_list(self, row):
        job = self.jobs[row]
        pages_dir = job.work_dir / "pages" if job.work_dir else None
        self.page_images = sorted(str(p) for p in pages_dir.glob("*.png")) if pages_dir and pages_dir.exists() else []
        n = len(self.page_images) or job.n_pages
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, n))
        self.page_spin.blockSignals(False)
        self.page_info.setText(f"of {n}")

    def _page_list_index(self, png: str) -> int:
        try:
            return self.page_images.index(str(Path(png)))
        except ValueError:
            return -1

    def on_page_spin(self, v):
        if self.page_images and v - 1 < len(self.page_images):
            self.page_view.show_image(self.page_images[v - 1])
        elif 0 <= self.shown_row < len(self.jobs):
            self._show_pdf_page(self.jobs[self.shown_row], v - 1)

    def _show_pdf_page(self, job: FileJob, index: int):
        """Quick low-res thumbnail for files that haven't been rendered yet."""
        import pymupdf as fitz
        thumb = self.tmp_root / f"thumb_{abs(hash(str(job.path)))}_{index}.png"
        try:
            if not thumb.exists():
                if job.path.suffix.lower() == ".pdf":
                    with fitz.open(job.path) as d:
                        d[index].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(thumb)
                else:
                    thumb = job.path
            self.page_view.show_image(str(thumb))
        except Exception:  # noqa: BLE001
            self.page_view.show_image(None)

    def _set_editor(self, text: str):
        self._loading_editor = True
        vpos = self.editor.verticalScrollBar().value()
        self.editor.setPlainText(text)
        self.editor.verticalScrollBar().setValue(vpos if not self.running() else
                                                 self.editor.verticalScrollBar().maximum())
        self._loading_editor = False

    def on_editor_changed(self):
        if self._loading_editor or self.running():
            return
        job = self.current_job()
        if job and (job.pages or job.edited_md is not None):
            job.edited_md = self.editor.toPlainText()
            job.saved = False
            self.preview_timer.start()

    def refresh_preview(self):
        job = self.jobs[self.shown_row] if 0 <= self.shown_row < len(self.jobs) else None
        md = job.markdown(self.settings()) if job else ""
        if not HAVE_WEBENGINE:
            self.preview.setMarkdown(md)
            return
        if not job or not job.work_dir:
            self.preview.setHtml("<body style='font-family:Segoe UI;color:gray;padding:24px'>"
                                 "The rendered Markdown (with equations) appears here.</body>")
            return
        html_path = job.work_dir / "preview.html"
        html_path.write_text(render_html(md, job.path.stem), encoding="utf-8")
        to_bottom = "true" if self.running() else "false"
        page = self.preview.page()

        def reload(scroll_y):
            y = scroll_y if isinstance(scroll_y, (int, float)) else 0

            def restore(ok):
                page.loadFinished.disconnect(restore)
                page.runJavaScript(
                    f"(function(){{var go=function(){{window.scrollTo(0,{to_bottom}?document.body.scrollHeight:{y});}};"
                    f"go(); if(window.MathJax&&MathJax.startup)MathJax.startup.promise.then(go);}})()")
            page.loadFinished.connect(restore)
            self.preview.load(QUrl.fromLocalFile(str(html_path)))

        page.runJavaScript("window.scrollY", 0, reload)

    # ------------------------------------------------------------------ saving
    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_dir.text() or str(Path.home()))
        if d:
            self.out_dir.setText(d)

    def save_as(self):
        job = self.current_job()
        if not job or not (job.pages or job.edited_md):
            return
        doc = job.current_doc(self.settings())
        start_dir = self.qs.value("last_out", str(job.path.parent))
        filters = "Markdown (*.md);;Plain text (*.txt);;HTML with rendered math (*.html)"
        dest, flt = QFileDialog.getSaveFileName(self, "Save as", str(Path(start_dir) / f"{job.path.stem}.md"),
                                                filters)
        if not dest:
            return
        dest = Path(dest)
        if not dest.suffix:
            dest = dest.with_suffix({"Plain": ".txt", "HTML ": ".html"}.get(flt[:6].rstrip("( "), ".md")
                                    if flt else ".md")
        try:
            out = save_document(doc, dest, job.edited_md)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{e}")
            return
        job.saved = True
        self.qs.setValue("last_out", str(dest.parent))
        self.statusBar().showMessage(f"Saved {out}")

    def save_all(self):
        done = [j for j in self.jobs if j.doc]
        if not done:
            return
        d = QFileDialog.getExistingDirectory(self, "Save all finished files to…",
                                             self.qs.value("last_out", str(done[0].path.parent)))
        if not d:
            return
        ext = self.fmt.currentText()
        for j in done:
            save_document(j.doc, Path(d) / f"{j.path.stem}.{ext}", j.edited_md)
            j.saved = True
        self.qs.setValue("last_out", d)
        self.statusBar().showMessage(f"Saved {len(done)} file(s) as .{ext} to {d}")

    def copy_markdown(self):
        job = self.current_job()
        if job:
            QApplication.clipboard().setText(job.markdown(self.settings()))
            self.statusBar().showMessage("Markdown copied to clipboard", 3000)

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, e):
        unsaved = [j for j in self.jobs if (j.pages or j.edited_md) and not j.saved]
        if self.running() or unsaved:
            what = "A conversion is running" if self.running() else f"{len(unsaved)} result(s) are not saved"
            if QMessageBox.question(self, APP_NAME, f"{what}. Quit anyway?") != QMessageBox.Yes:
                e.ignore()
                return
        if self.running():
            self.worker.stop()
            self.worker.wait(15000)
        self._save_prefs()
        shutil.rmtree(self.tmp_root, ignore_errors=True)
        e.accept()


def fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def main():
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-compositing")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    w.add_files([a for a in sys.argv[1:] if Path(a).exists()])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
