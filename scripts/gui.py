"""Windows GUI for the validated Andor Solis SIF batch exporter."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import math
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

import batch_export_sif as batch
import intensity_analysis as intensity

VERSION = "1.1.0"
DEVELOPER = "Developed by Wenjia Zhang, Department of Chemistry, Fudan University."
USAGE_NOTICE = "For research use only. Not for commercial use."


class _QueueWriter(StringIO):
    """将核心处理程序的控制台日志逐行转发给主线程。"""

    def __init__(self, messages: queue.Queue[dict[str, Any]]) -> None:
        super().__init__()
        self.messages = messages
        self.pending = ""

    def write(self, value: str) -> int:
        self.pending += value
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line:
                self.messages.put({"type": "log", "text": line.rstrip()})
        return len(value)

    def flush(self) -> None:
        if self.pending.strip():
            self.messages.put({"type": "log", "text": self.pending.rstrip()})
        self.pending = ""


def build_options(input_dir: str, output_dir: str, display_min: str, display_max: str,
                  tiff8: bool, tiff16: bool, recursive: bool, raw_tiff: bool = False, png: bool = False,
                  data_range: str = "custom") -> dict[str, Any]:
    """验证界面参数并生成核心批处理所需的选项。"""
    input_text, output_text = input_dir.strip(), output_dir.strip()
    if not input_text:
        raise ValueError("Select an input folder.")
    source = Path(input_text).expanduser()
    if not source.is_dir():
        raise ValueError("The input folder does not exist or is not a folder.")
    if not output_text:
        raise ValueError("Select an output folder.")
    if not any((tiff8, tiff16, raw_tiff, png)):
        raise ValueError("Select at least one export format.")
    low, high = 0.0, 65535.0
    if any((tiff8, tiff16, png)):
        if data_range not in {"custom", "minmax", "0-65535"}:
            raise ValueError("Select a Data Range mode.")
        if data_range == "custom":
            if not display_min.strip() or not display_max.strip():
                raise ValueError("Enter both Display Min and Display Max.")
            try:
                low, high = float(display_min), float(display_max)
            except ValueError as exc:
                raise ValueError("Display Min and Display Max must be valid numbers.") from exc
            if not math.isfinite(low) or not math.isfinite(high):
                raise ValueError("Display Min and Display Max must be finite numbers.")
            if high <= low:
                raise ValueError("Display Max must be greater than Display Min.")
    return {"input": source, "input_file": None, "output": Path(output_text).expanduser(),
            "display_min": low, "display_max": high, "recursive": bool(recursive),
            "png": bool(png), "data_range": data_range, "display_tiff": bool(tiff8), "tiff16": bool(tiff16),
            "raw_tiff": bool(raw_tiff), "display_tiff_orientation": "solis-compatible"}


class ExportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Andor SIF Batch Export v{VERSION}")
        self.root.minsize(780, 860)
        self.root.geometry("820x900+40+40")
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.active = False

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.min_var = tk.StringVar()
        self.max_var = tk.StringVar()
        self.tiff8_var = tk.BooleanVar(value=False)
        self.tiff16_var = tk.BooleanVar(value=False)
        self.raw_var = tk.BooleanVar(value=False)
        self.png_var = tk.BooleanVar(value=False)
        self.range_var = tk.StringVar(value="")
        self.recursive_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.StringVar(value="0 / 0 files")
        self.success_var = tk.StringVar(value="Successful: 0")
        self.failure_var = tk.StringVar(value="Failed: 0")
        self.report_path: Path | None = None
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_messages)

    def _build_ui(self) -> None:
        self.root.configure(padx=20, pady=18)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Hint.TLabel", foreground="#596579")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(16, 8))

        # 先预留底部空间，日志随窗口伸缩，署名始终可见。
        footer = ttk.Frame(self.root)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.footer_labels = []
        for text in (DEVELOPER, USAGE_NOTICE):
            label = ttk.Label(footer, text=text, style="Hint.TLabel", anchor="center")
            label.pack(fill="x")
            self.footer_labels.append(label)

        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="Andor SIF Batch Export", style="Title.TLabel").pack(anchor="w", pady=(0, 14))

        self._path_row(main, "Input folder", self.input_var, self._browse_input)
        self._path_row(main, "Output folder", self.output_var, self._browse_output)

        range_frame = ttk.LabelFrame(main, text="Data range", padding=(12, 8))
        range_frame.pack(fill="x", pady=(0, 12))
        for index, (label, value) in enumerate((("Custom Min–Max", "custom"), ("Min–Max (Solis Batch Conversion)", "minmax"), ("0–65535 (Solis Batch Conversion)", "0-65535"))):
            ttk.Radiobutton(range_frame, text=label, variable=self.range_var, value=value,
                            command=self._range_changed).grid(row=index, column=0, columnspan=2, sticky="w")
        ttk.Label(range_frame, text="Min–Max adjusts each image separately; avoid comparing fluorescence brightness.", style="Hint.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        range_frame.columnconfigure(0, weight=1)
        range_frame.columnconfigure(1, weight=1)
        ttk.Label(range_frame, text="Display Min").grid(row=4, column=0, sticky="w", padx=(0, 10))
        ttk.Label(range_frame, text="Display Max").grid(row=4, column=1, sticky="w", padx=(10, 0))
        self.min_entry = ttk.Entry(range_frame, textvariable=self.min_var)
        self.max_entry = ttk.Entry(range_frame, textvariable=self.max_var)
        self.min_entry.grid(row=5, column=0, sticky="ew", padx=(0, 10), pady=(4, 0), ipady=3)
        self.max_entry.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=(4, 0), ipady=3)

        self.analyze_button = ttk.Button(range_frame, text="Analyze SIF Intensity", command=self.start_analysis)
        self.analyze_button.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._range_changed()

        options = ttk.Frame(main)
        options.pack(fill="x", pady=(0, 12))
        formats = ttk.LabelFrame(options, text="Export format", padding=(12, 8))
        formats.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ttk.Checkbutton(formats, text="8-bit TIFF", variable=self.tiff8_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(formats, text="16-bit TIFF", variable=self.tiff16_var).pack(side="left")
        ttk.Checkbutton(formats, text="Raw TIFF", variable=self.raw_var).pack(side="left", padx=12)
        ttk.Checkbutton(formats, text="PNG", variable=self.png_var).pack(side="left")
        batch_options = ttk.LabelFrame(options, text="Batch options", padding=(12, 8))
        batch_options.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Checkbutton(batch_options, text="Include subfolders", variable=self.recursive_var).pack(anchor="w")

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(buttons, text="Start Export", style="Primary.TButton", command=self.start_export)
        self.start_button.pack(side="left")
        self.open_button = ttk.Button(buttons, text="Open Output Folder", command=self.open_output_folder)
        self.open_button.pack(side="left", padx=(10, 0))

        progress = ttk.LabelFrame(main, text="Progress", padding=(12, 9))
        progress.pack(fill="x", pady=(0, 12))
        self.progressbar = ttk.Progressbar(progress, mode="determinate", maximum=1, value=0)
        self.progressbar.pack(fill="x", pady=(0, 7))
        counters = ttk.Frame(progress)
        counters.pack(fill="x")
        ttk.Label(counters, textvariable=self.progress_var).pack(side="left")
        ttk.Label(counters, textvariable=self.success_var).pack(side="left", padx=(20, 0))
        ttk.Label(counters, textvariable=self.failure_var).pack(side="left", padx=(20, 0))
        ttk.Label(progress, textvariable=self.status_var, style="Hint.TLabel").pack(anchor="w", pady=(7, 0))

        log_frame = ttk.LabelFrame(main, text="Activity log", padding=(8, 7))
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=9, wrap="word", state="disabled", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, browse: Any) -> None:
        group = ttk.LabelFrame(parent, text=label, padding=(12, 7))
        group.pack(fill="x", pady=(0, 11))
        group.columnconfigure(0, weight=1)
        entry = ttk.Entry(group, textvariable=variable)
        entry.grid(row=0, column=0, sticky="ew", ipady=3)
        ttk.Button(group, text="Browse...", command=browse).grid(row=0, column=1, padx=(9, 0))

    def _browse_input(self) -> None:
        selected = filedialog.askdirectory(title="Select input folder")
        if selected:
            self.input_var.set(selected)

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Select output folder")
        if selected:
            self.output_var.set(selected)

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _range_changed(self) -> None:
        state = "normal" if self.range_var.get() == "custom" else "disabled"
        self.min_entry.configure(state=state)
        self.max_entry.configure(state=state)

    def start_export(self) -> None:
        if self.active:
            return
        try:
            options = build_options(self.input_var.get(), self.output_var.get(), self.min_var.get(),
                                    self.max_var.get(), self.tiff8_var.get(), self.tiff16_var.get(),
                                    self.recursive_var.get(), self.raw_var.get(), self.png_var.get(), self.range_var.get())
        except ValueError as exc:
            messagebox.showerror("Cannot start export", str(exc), parent=self.root)
            return
        self.active = True
        self.report_path = None
        self.start_button.configure(state="disabled")
        self.analyze_button.configure(state="disabled")
        self.status_var.set("Scanning input folder...")
        self.progress_var.set("0 / 0 files")
        self.success_var.set("Successful: 0")
        self.failure_var.set("Failed: 0")
        self.progressbar.configure(maximum=1, value=0)
        self._append_log("Scanning input folder...")
        self.worker = threading.Thread(target=self._run_export, args=(options,), daemon=True)
        self.worker.start()

    def start_analysis(self) -> None:
        if self.active:
            return
        source = Path(self.input_var.get().strip()).expanduser()
        if not self.input_var.get().strip() or not source.is_dir():
            messagebox.showerror("Cannot start analysis", "Select an existing input folder.", parent=self.root)
            return
        self.active = True
        self.start_button.configure(state="disabled")
        self.analyze_button.configure(state="disabled")
        self.progressbar.configure(maximum=1, value=0)
        self.progress_var.set("0 / 0 files")
        self.success_var.set("Successful: 0")
        self.failure_var.set("Failed: 0")
        self.status_var.set("Scanning input folder for analysis...")
        self.worker = threading.Thread(target=self._run_analysis,
                                       args=(source, self.recursive_var.get()), daemon=True)
        self.worker.start()

    def _run_analysis(self, source: Path, recursive: bool) -> None:
        try:
            result = intensity.analyze_intensity(source, recursive, lambda event:
                self.messages.put({"type": "analysis_progress", "event": event}))
            for row in result["rows"]:
                if row["status"] != "OK":
                    self.messages.put({"type": "log", "text": f"{row['file']} frame {row['frame']}: {row['error']}"})
            self.messages.put({"type": "analysis_result", "result": result})
        except Exception as exc:
            self.messages.put({"type": "error", "text": str(exc)})

    def _run_export(self, options: dict[str, Any]) -> None:
        try:
            files = batch.discover_sif_files(options["input"], options["recursive"])
            if not files:
                raise FileNotFoundError("No .sif files were found in the selected input folder.")
            options["_files"] = files
            writer = _QueueWriter(self.messages)
            with redirect_stdout(writer):
                result = batch.run_batch(options, progress_callback=lambda event: self.messages.put(
                    {"type": "progress", "event": event}))
                writer.flush()
            self.messages.put({"type": "result", "result": result})
        except Exception as exc:
            self.messages.put({"type": "error", "text": str(exc)})

    def _poll_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message["type"]
                if kind == "log":
                    self._append_log(message["text"])
                elif kind == "progress":
                    self._update_progress(message["event"])
                elif kind == "analysis_progress":
                    event = message["event"]
                    self.progressbar.configure(maximum=max(event["total"], 1), value=event["processed"])
                    self.progress_var.set(f"{event['processed']} / {event['total']} files")
                    self.success_var.set(f"Successful: {event['succeeded']}")
                    self.failure_var.set(f"Failed: {event['failed']}")
                    self.status_var.set("Analyzing: " + event.get("source", "Scanning complete"))
                elif kind == "analysis_result":
                    self.active = False
                    self.start_button.configure(state="normal")
                    self.analyze_button.configure(state="normal")
                    self.status_var.set("Analysis completed")
                    self.analysis_window = AnalysisWindow(self.root, message["result"])
                elif kind == "result":
                    files, succeeded, failed, report = message["result"]
                    self._finish(files, succeeded, failed, report)
                elif kind == "error":
                    self.active = False
                    self.start_button.configure(state="normal")
                    self.analyze_button.configure(state="normal")
                    self.status_var.set("Task stopped")
                    self._append_log("ERROR: " + message["text"])
                    messagebox.showerror("Task failed", message["text"], parent=self.root)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._poll_messages)

    def _update_progress(self, event: dict[str, Any]) -> None:
        if event["phase"] == "start":
            total = event["total"]
            self.progressbar.configure(maximum=max(total, 1), value=0)
            self.progress_var.set(f"0 / {total} files")
            self.status_var.set("Exporting...")
            self.report_path = Path(event["report"])
        elif event["phase"] == "file":
            processed, total = event["processed"], event["total"]
            self.progressbar.configure(maximum=max(total, 1), value=processed)
            self.progress_var.set(f"{processed} / {total} files")
            self.success_var.set(f"Successful: {event['succeeded']}")
            self.failure_var.set(f"Failed: {event['failed']}")
            self.status_var.set(f"Processed {Path(event['source']).name}: {event['status']}")
        elif event["phase"] == "finished":
            self.report_path = Path(event["report"])

    def _finish(self, files: int, succeeded: int, failed: int, report: Path) -> None:
        self.active = False
        self.start_button.configure(state="normal")
        self.analyze_button.configure(state="normal")
        self.report_path = report
        self.progress_var.set(f"{files} / {files} files")
        self.success_var.set(f"Successful: {succeeded}")
        self.failure_var.set(f"Failed: {failed}")
        self.status_var.set("Export completed" if not failed else "Export completed with errors")
        self._append_log(f"Result: {succeeded} successful, {failed} failed.")
        self._append_log(f"Audit report: {report}")
        messagebox.showinfo("Export complete", f"Files: {files}\nSuccessful: {succeeded}\nFailed: {failed}\n\nAudit report:\n{report}", parent=self.root)

    def open_output_folder(self) -> None:
        path_text = self.output_var.get().strip()
        if not path_text:
            messagebox.showerror("Output folder", "Select an output folder first.", parent=self.root)
            return
        path = Path(path_text).expanduser()
        if not path.is_dir():
            messagebox.showerror("Output folder", "The selected output folder does not exist.", parent=self.root)
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows Explorer
        except OSError as exc:
            messagebox.showerror("Cannot open folder", str(exc), parent=self.root)

    def _close(self) -> None:
        if self.active:
            messagebox.showwarning("Task in progress", "Wait for the current task to finish before closing.", parent=self.root)
            return
        self.root.destroy()


def format_intensity(value: Any) -> str:
    return "N/A" if value is None else format(value, ".12g")


class AnalysisWindow:
    def __init__(self, parent: tk.Tk, result: dict[str, Any]) -> None:
        self.result = result
        self.window = tk.Toplevel(parent)
        self.window.title("Image Intensity Analysis")
        self.window.geometry("1100x700")
        self.window.minsize(850, 560)
        body = ttk.Frame(self.window, padding=16)
        body.pack(fill="both", expand=True)
        summary = result["summary"]
        box = ttk.LabelFrame(body, text="Analysis summary", padding=12)
        box.pack(fill="x")
        labels = (("Files analyzed", "files_analyzed"), ("Frames analyzed", "frames_analyzed"),
                  ("Lowest minimum", "lowest_minimum"), ("Highest minimum", "highest_minimum"),
                  ("Lowest maximum", "lowest_maximum"), ("Highest maximum", "highest_maximum"),
                  ("Median of image minima", "median_of_image_minima"),
                  ("Median of image maxima", "median_of_image_maxima"))
        for index, (label, key) in enumerate(labels):
            row, pair = divmod(index, 2)
            ttk.Label(box, text=label + ":").grid(row=row, column=pair * 2, sticky="w", padx=(0, 12), pady=3)
            ttk.Label(box, text=format_intensity(summary[key])).grid(row=row, column=pair * 2 + 1, sticky="w", padx=(0, 35))
        for col in (1, 3):
            box.columnconfigure(col, weight=1)
        overall = "N/A (no valid data)" if not summary["frames_analyzed"] else (
            format_intensity(summary["lowest_minimum"]) + " – " + format_intensity(summary["highest_maximum"]))
        ttk.Label(box, text="Overall intensity range: " + overall).grid(row=4, column=0, columnspan=4, sticky="w", pady=5)
        ttk.Label(box, text=f"Files found: {summary['files_found']}    Fully successful: {summary['files_succeeded']}    "
                  f"Failed / partial: {summary['files_failed']}    Frames excluded: {summary['frames_excluded']}    "
                  f"NaN/Inf pixels: {summary['invalid_pixels']}").grid(row=5, column=0, columnspan=4, sticky="w")
        ttk.Label(body, text="Raw pixels only. Nonfinite frames excluded; valid sibling frames retained. Analyzed / failed files may overlap.",
                  style="Hint.TLabel").pack(anchor="w", pady=(10, 3))
        ttk.Label(body, text="Reference only: Custom Min/Max stays unchanged. P1/P99 are not recommended display limits.",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 10))
        table = ttk.LabelFrame(body, text="Per-image statistics", padding=6)
        table.pack(fill="both", expand=True)
        columns = ("file", "frame", *intensity.STAT_KEYS, "status", "invalid_pixels", "error")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=12)
        self.sort_reverse = {}
        for column in columns:
            self.tree.heading(column, text={"file": "File", "frame": "Frame", "p1": "P1", "p99": "P99",
                              "invalid_pixels": "NaN/Inf pixels"}.get(column, column.title()),
                              command=lambda c=column: self.sort(c))
            self.tree.column(column, width=250 if column in ("file", "error") else 110, minwidth=65, stretch=False)
        for row in result["rows"]:
            values = [row.get(c, "") if c not in intensity.STAT_KEYS else
                      (format_intensity(row[c]) if c in row else "N/A") for c in columns]
            self.tree.insert("", "end", values=values)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        self.csv_button = ttk.Button(buttons, text="Export CSV", command=self.save_csv)
        self.csv_button.pack(side="left")
        ttk.Button(buttons, text="Close", command=self.window.destroy).pack(side="right")

    def sort(self, column: str) -> None:
        reverse = self.sort_reverse.get(column, False)
        def key(item):
            value = self.tree.set(item, column)
            if column in (*intensity.STAT_KEYS, "frame", "invalid_pixels"):
                return (value in ("", "N/A"), float(value) if value not in ("", "N/A") else 0)
            return value.casefold()
        for index, item in enumerate(sorted(self.tree.get_children(), key=key, reverse=reverse)):
            self.tree.move(item, "", index)
        self.sort_reverse[column] = not reverse

    def save_csv(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.window, title="Export intensity analysis CSV",
                                          defaultextension=".csv", filetypes=[("CSV files", "*.csv")],
                                          initialfile="sif_intensity_analysis.csv")
        if path:
            try:
                intensity.export_csv(self.result, Path(path))
                messagebox.showinfo("CSV exported", str(path), parent=self.window)
            except Exception as exc:
                messagebox.showerror("CSV export failed", str(exc), parent=self.window)


def main() -> None:
    # 实际发行 EXE 的无界面批量验收入口，复用 CLI 参数和同一个核心。
    if len(sys.argv) > 1 and sys.argv[1] == "--batch":
        args = sys.argv[2:]
        options = batch.resolve_options(batch.build_parser().parse_args(args))
        log_dir = Path(options["output"]) / "reports"
        log_dir.mkdir(parents=True, exist_ok=True)
        from contextlib import redirect_stderr
        with (log_dir / "frozen_console.txt").open("x", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                raise SystemExit(batch.main(args))
    root = tk.Tk()
    app = ExportApp(root)
    if len(sys.argv) == 4 and sys.argv[1] == "--check-analysis":
        # 冻结程序验收：真实 Tk 事件循环、后台线程、按钮及保存处理函数。
        # 仅在诊断模式替换系统对话框，避免无人值守验收等待人工输入。
        import json
        import time
        report_dir = Path(sys.argv[3])
        report_dir.mkdir(parents=True, exist_ok=False)
        app.input_var.set(sys.argv[2])
        app.min_var.set("12694")
        app.max_var.set("28977")
        initial = (app.min_var.get(), app.max_var.get())
        started = time.monotonic()
        state = {"event_loop_ticks": 0, "dialogs": [], "errors": []}
        root.update()
        state["version"] = VERSION
        state["footer"] = [label.cget("text") for label in app.footer_labels]
        def capture(window, filename):
            # 仅诊断入口按需截取本程序窗口；不改变正常 GUI 或科研数据。
            if os.environ.get("ANDOR_QA_SCREENSHOTS") == "1":
                from PIL import ImageGrab
                window.update()
                ImageGrab.grab(window=window.winfo_id()).save(report_dir / filename)
        if os.environ.get("ANDOR_QA_SCREENSHOTS") == "1":
            for size, name in (("820x900+40+40", "main_default.png"),
                               ("780x860+40+40", "main_minimum.png"),
                               ("1100x950+40+40", "main_enlarged.png")):
                root.geometry(size)
                capture(root, name)
            root.geometry("820x900+40+40")
        messagebox.showinfo = lambda *args, **kwargs: state["dialogs"].append(list(args))
        messagebox.showerror = lambda *args, **kwargs: state["errors"].append(list(args))
        filedialog.asksaveasfilename = lambda **kwargs: str(report_dir / "intensity.csv")
        phase = "analysis"
        def check():
            nonlocal phase
            state["event_loop_ticks"] += 1
            try:
                if time.monotonic() - started > 60:
                    raise TimeoutError("Frozen GUI check timed out")
                if not app.active:
                    if phase == "analysis":
                        window = app.analysis_window
                        capture(window.window, "analysis_results.png")
                        state["analysis"] = window.result
                        state["tree_rows"] = [window.tree.item(item, "values") for item in window.tree.get_children()]
                        state["custom_unchanged"] = initial == (app.min_var.get(), app.max_var.get())
                        state["independent_options"] = not app.output_var.get() and not app.range_var.get() and not any(
                            v.get() for v in (app.tiff8_var, app.tiff16_var, app.raw_var, app.png_var))
                        window.csv_button.invoke()
                        state["csv_exists"] = (report_dir / "intensity.csv").is_file()
                        window.sort("min")
                        state["sorted_minima"] = [float(window.tree.set(item, "min")) for item in window.tree.get_children()]
                        phase = "export"
                        app.output_var.set(str(report_dir / "export"))
                        app.range_var.set("custom")
                        for var in (app.tiff8_var, app.tiff16_var, app.raw_var, app.png_var):
                            var.set(True)
                        app.start_button.invoke()
                    else:
                        state["export_success"] = app.success_var.get()
                        state["export_failed"] = app.failure_var.get()
                        state["elapsed_seconds"] = time.monotonic() - started
                        (report_dir / "gui_analysis.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
                        root.destroy()
                        return
                root.after(10, check)
            except Exception as exc:
                state["errors"].append(str(exc))
                (report_dir / "gui_analysis.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
                root.destroy()
        app.analyze_button.invoke()
        root.after(10, check)
        root.mainloop()
        if state["errors"]:
            raise SystemExit(1)
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--check-gui":
        import json
        root.update()
        snapshot = {"blank_inputs": all(not v.get() for v in (app.input_var, app.output_var, app.min_var, app.max_var)),
                    "unchecked_formats": all(not v.get() for v in (app.tiff8_var, app.tiff16_var, app.raw_var, app.png_var)),
                    "data_range": app.range_var.get(), "recursive": app.recursive_var.get(),
                    "min_state": str(app.min_entry.cget("state")), "size": root.geometry()}
        with Path(sys.argv[2]).open("x", encoding="utf-8") as stream:
            json.dump(snapshot, stream, indent=2)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
