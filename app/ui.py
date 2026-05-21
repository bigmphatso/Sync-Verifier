from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk

from .reporter import export_report
from .scanner import (
    FileIssue,
    OneDriveStatus,
    ScanResult,
    SyncScanner,
    check_onedrive_sync_status,
    default_profile_paths,
    resolve_onedrive_compare_root,
    safe_to_wipe_paths,
)

PRIMARY_FOLDERS = ("Desktop", "Documents", "Pictures")


class DetailsWindow:
    def __init__(self, parent: Tk, result: ScanResult) -> None:
        self.result = result
        self.window = Toplevel(parent)
        self.window.title("File Details")
        self.window.geometry("1040x520")
        self.window.minsize(820, 420)

        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)

        columns = ("file", "issue", "local", "cloud", "severity")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.pack(side="right", fill="y")
        tree.configure(yscrollcommand=scrollbar.set)

        for column, heading, width in (
            ("file", "File", 520),
            ("issue", "Issue", 170),
            ("local", "Local Size", 100),
            ("cloud", "OneDrive Size", 120),
            ("severity", "Severity", 90),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, stretch=column == "file")

        for issue in result.issues:
            tree.insert(
                "",
                "end",
                values=(
                    issue.file_path,
                    issue.issue_type.replace("_", " ").title(),
                    self._display_size(issue.local_size),
                    self._display_size(issue.cloud_size),
                    issue.severity.upper(),
                ),
            )

    def _display_size(self, size: int | None) -> str:
        if size is None:
            return "-"
        units = ("B", "KB", "MB", "GB", "TB")
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024
        return str(size)


class SyncVerifierApp:
    def __init__(self) -> None:
        self.scanner = SyncScanner()
        self.root = Tk()
        self.root.title("Sync Integrity Verifier")
        self.root.geometry("1040x760")
        self.root.minsize(940, 720)

        self.onedrive_status: OneDriveStatus | None = None
        self.scope = StringVar(value="primary")
        self.custom_path = StringVar(value=str(Path.home()))
        self.one_drive_text = StringVar(value="Looking for OneDrive...")
        self.scope_text = StringVar(value="Desktop, Documents, and Pictures")
        self.target_text = StringVar(value="Target will appear after OneDrive is found.")
        self.result_title = StringVar(value="Ready")
        self.result_detail = StringVar(value="Choose what to compare, then run the check.")
        self.progress_text = StringVar(value="No scan running")
        self.verified_text = StringVar(value="0")
        self.compared_text = StringVar(value="0")
        self.issues_text = StringVar(value="0")
        self.cloud_text = StringVar(value="0")
        self.hash_verify = BooleanVar(value=False)

        self.current_result: ScanResult | None = None
        self.scan_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self._loading = True
        self._spinner = itertools.cycle(("Looking for OneDrive", "Looking for OneDrive.", "Looking for OneDrive.."))

        self._style()
        self._build()
        self._detect_onedrive_async()
        self._animate_detection()

    def _style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.root.configure(bg="#fbfcfd")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#fbfcfd")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#fbfcfd", foreground="#18212b")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#18212b")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667789")
        style.configure("Title.TLabel", background="#fbfcfd", foreground="#18212b", font=("Segoe UI Semibold", 20))
        style.configure("Step.TLabel", background="#ffffff", foreground="#18212b", font=("Segoe UI Semibold", 13))
        style.configure("Metric.TLabel", background="#ffffff", foreground="#18212b", font=("Segoe UI Semibold", 20))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(18, 9))
        style.configure("TButton", padding=(12, 8))
        style.configure("TRadiobutton", background="#ffffff")
        style.configure("TCheckbutton", background="#ffffff")
        style.configure("Horizontal.TProgressbar", thickness=10)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Sync Integrity Verifier", style="Title.TLabel").pack(anchor="w", pady=(0, 14))

        top = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="1. OneDrive folder", style="Step.TLabel").pack(anchor="w")
        ttk.Label(top, textvariable=self.one_drive_text, style="Muted.TLabel", wraplength=880).pack(anchor="w", pady=(6, 10))
        self.detect_bar = ttk.Progressbar(top, orient="horizontal", mode="indeterminate")
        self.detect_bar.pack(fill="x")
        self.detect_bar.start(14)

        middle = ttk.Frame(outer)
        middle.pack(fill="both", expand=True)

        left = ttk.Frame(middle, style="Panel.TFrame", padding=16)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        ttk.Label(left, text="2. Choose what to compare", style="Step.TLabel").pack(anchor="w")
        self._scope_option(left, "primary", "Default folders", "Desktop, Documents, and Pictures")
        self._scope_option(left, "profile", "Entire user profile", str(Path.home()))
        self._scope_option(left, "drive", "Entire drive", Path.home().anchor or "Current drive")
        self._scope_option(left, "custom", "Custom folder", self.custom_path.get())
        ttk.Button(left, text="Browse custom folder", command=self.select_custom_path).pack(anchor="w", pady=(12, 0))
        ttk.Label(left, textvariable=self.scope_text, style="Muted.TLabel", wraplength=430).pack(anchor="w", pady=(12, 0))
        ttk.Separator(left).pack(fill="x", pady=14)
        ttk.Label(left, text="3. Confirm and scan", style="Step.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.target_text, style="Muted.TLabel", wraplength=430).pack(anchor="w", pady=(6, 10))
        ttk.Checkbutton(left, text="Hash verify files after size checks", variable=self.hash_verify).pack(anchor="w")
        buttons = ttk.Frame(left, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Run Check", style="Primary.TButton", command=self.start_scan).pack(side="left")
        ttk.Button(buttons, text="Safe To Wipe", command=self.safe_to_wipe_check).pack(side="left", padx=(8, 0))

        right = ttk.Frame(middle, style="Panel.TFrame", padding=16)
        right.pack(side="right", fill="both", expand=True)
        ttk.Label(right, textvariable=self.result_title, style="Step.TLabel").pack(anchor="w")
        ttk.Label(right, textvariable=self.result_detail, style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(6, 12))

        metrics = ttk.Frame(right, style="Panel.TFrame")
        metrics.pack(fill="x", pady=(0, 12))
        self._metric(metrics, "Files OK", self.verified_text).grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))
        self._metric(metrics, "Files checked", self.compared_text).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))
        self._metric(metrics, "Need attention", self.issues_text).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._metric(metrics, "Online only", self.cloud_text).grid(row=1, column=1, sticky="ew", padx=(8, 0))
        metrics.columnconfigure((0, 1), weight=1)

        self.scan_bar = ttk.Progressbar(right, orient="horizontal", mode="determinate")
        self.scan_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(right, textvariable=self.progress_text, style="Muted.TLabel").pack(anchor="w")

        self.report = ttk.Label(right, text="No report yet.", style="Panel.TLabel", justify="left", anchor="nw", wraplength=430)
        self.report.pack(fill="both", expand=True, pady=(14, 0))

        footer = ttk.Frame(right, style="Panel.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Details", command=self.view_details).pack(side="left")
        ttk.Button(footer, text="Export", command=self.export_current_report).pack(side="left", padx=(8, 0))
        ttk.Button(footer, text="Cancel", command=self.cancel_scan).pack(side="right")

    def _scope_option(self, parent: ttk.Frame, value: str, title: str, hint: str) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Radiobutton(row, text=title, value=value, variable=self.scope, command=self._scope_changed).pack(side="left")
        ttk.Label(row, text=hint, style="Muted.TLabel").pack(side="right")

    def _metric(self, parent: ttk.Frame, title: str, value: StringVar) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        ttk.Label(frame, textvariable=value, style="Metric.TLabel").pack(anchor="w")
        ttk.Label(frame, text=title, style="Muted.TLabel").pack(anchor="w")
        return frame

    def _detect_onedrive_async(self) -> None:
        self._loading = True
        threading.Thread(target=self._detect_onedrive, daemon=True).start()

    def _detect_onedrive(self) -> None:
        time.sleep(0.35)
        status = check_onedrive_sync_status()
        self.root.after(0, lambda: self._finish_detection(status))

    def _finish_detection(self, status: OneDriveStatus) -> None:
        self.onedrive_status = status
        self._loading = False
        self.detect_bar.stop()
        if status.root and status.sync_available:
            self.one_drive_text.set(f"Found: {status.root}\n{status.reason}")
        else:
            self.one_drive_text.set(status.reason)
        self._update_scope()

    def _animate_detection(self) -> None:
        if self._loading:
            self.one_drive_text.set(next(self._spinner))
        self.root.after(240, self._animate_detection)

    def _scope_changed(self) -> None:
        if self.scope.get() == "custom":
            self.select_custom_path()
        self._update_scope()

    def _update_scope(self) -> None:
        paths = self._selected_paths()
        if self.scope.get() == "primary":
            found_names = [path.name for path in paths]
            missing = [name for name in PRIMARY_FOLDERS if name not in found_names]
            message = "Default folders found:\n" + "\n".join(f"- {path.name}: {path}" for path in paths)
            if missing:
                message += "\n\nNot found on this PC: " + ", ".join(missing)
            self.scope_text.set(message)
        elif len(paths) == 1:
            self.scope_text.set(str(paths[0]))
        else:
            self.scope_text.set("; ".join(str(path) for path in paths))

        if self.onedrive_status and self.onedrive_status.root:
            targets = [resolve_onedrive_compare_root(path, self.onedrive_status.root) for path in paths]
            if len(targets) == 1:
                self.target_text.set(f"OneDrive target:\n{targets[0]}")
            else:
                self.target_text.set("OneDrive targets:\n" + "\n".join(f"- {target}" for target in targets))

    def _selected_paths(self) -> list[Path]:
        if self.scope.get() == "primary":
            return default_profile_paths()
        if self.scope.get() == "profile":
            return [Path.home()]
        if self.scope.get() == "drive":
            home = Path.home()
            return [Path(home.anchor or str(home))]
        return [Path(self.custom_path.get()).expanduser()]

    def select_custom_path(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.custom_path.get())
        if selected:
            self.custom_path.set(selected)
            self.scope.set("custom")
        self._update_scope()

    def _can_scan(self) -> bool:
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan Running", "A scan is already in progress.")
            return False
        if not self.onedrive_status or not self.onedrive_status.root:
            messagebox.showerror("OneDrive Not Found", "OneDrive folder could not be found.")
            return False
        return True

    def start_scan(self) -> None:
        if self._can_scan():
            self._run_paths(self._selected_paths(), "Checking files")

    def safe_to_wipe_check(self) -> None:
        if self._can_scan():
            paths = safe_to_wipe_paths()
            if not paths:
                messagebox.showwarning("No Folders", "Desktop, Documents, or Pictures could not be found.")
                return
            self._run_paths(paths, "Safe to wipe check")

    def _run_paths(self, paths: list[Path], title: str) -> None:
        self.cancel_event.clear()
        self.result_title.set(title)
        self.result_detail.set("Checking every file we can see in the selected folders.")
        self.progress_text.set("Starting...")
        self.scan_bar["value"] = 0
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(paths,), daemon=True)
        self.scan_thread.start()

    def _scan_worker(self, paths: list[Path]) -> None:
        assert self.onedrive_status and self.onedrive_status.root
        aggregate = ScanResult(directory="; ".join(str(path) for path in paths), hash_verification_enabled=self.hash_verify.get())
        aggregate.started_at = time.time()

        for index, path in enumerate(paths, start=1):
            if self.cancel_event.is_set():
                break
            compare_target = resolve_onedrive_compare_root(path.resolve(), self.onedrive_status.root)

            def progress(current: int, total: int, path_name: str = path.name or str(path), path_index: int = index) -> None:
                percent = ((path_index - 1) + (current / max(total, 1))) / max(len(paths), 1) * 100
                self.root.after(0, lambda: self._set_progress(percent, f"{path_name}: {current} of {total} files"))

            partial = self.scanner.scan(
                str(path),
                progress_callback=progress,
                cancel_event=self.cancel_event,
                compare_root=str(compare_target),
                hash_verify=self.hash_verify.get(),
            )
            if not aggregate.comparison_target:
                aggregate.comparison_target = str(compare_target)
            aggregate.folder_summaries.append(
                {
                    "name": path.name or str(path),
                    "path": str(path),
                    "target": str(compare_target),
                    "scanned": partial.scanned_files,
                    "verified": partial.verified_files,
                    "issues": partial.integrity_issues,
                    "cloud_only": partial.cloud_only_files,
                }
            )
            aggregate.scanned_files += partial.scanned_files
            aggregate.compared_files += partial.compared_files
            aggregate.hash_files_checked += partial.hash_files_checked
            aggregate.hash_mismatches += partial.hash_mismatches
            aggregate.verified_files += partial.verified_files
            aggregate.cloud_only_files += partial.cloud_only_files
            aggregate.integrity_issues += partial.integrity_issues
            aggregate.issues.extend(partial.issues)

        aggregate.risk_level, aggregate.recommendation = self.scanner._risk_profile(aggregate)
        aggregate.finished_at = time.time()
        self.root.after(0, lambda: self._show_result(aggregate, self.cancel_event.is_set()))

    def _set_progress(self, percent: float, text: str) -> None:
        self.scan_bar["value"] = percent
        self.progress_text.set(text)

    def _show_result(self, result: ScanResult, cancelled: bool) -> None:
        self.current_result = result
        self.scan_bar["value"] = 100 if not cancelled else self.scan_bar["value"]
        self.progress_text.set(f"{result.scanned_files} files checked in {result.duration_seconds:.1f}s")
        self.verified_text.set(str(result.verified_files))
        self.compared_text.set(str(result.compared_files))
        self.issues_text.set(str(result.integrity_issues))
        self.cloud_text.set(str(result.cloud_only_files))

        if cancelled:
            self.result_title.set("Cancelled")
            self.result_detail.set("The scan stopped before finishing.")
        elif result.risk_level == "LOW":
            self.result_title.set("Looks safe")
            self.result_detail.set("No missing, mismatched, or online-only files were found in the checked folders.")
        elif result.risk_level == "MEDIUM":
            self.result_title.set("Review before wiping")
            self.result_detail.set("Some files are online-only. Open OneDrive and make sure they are downloaded if you need them on this PC.")
        else:
            self.result_title.set("Do not wipe yet")
            self.result_detail.set("Some files do not appear to match OneDrive. Fix these before wiping or reinstalling.")

        self.report.configure(text=self._plain_report(result))

    def _plain_report(self, result: ScanResult) -> str:
        folder_lines = []
        for summary in result.folder_summaries:
            folder_lines.append(
                f"- {summary['name']}: checked {summary['scanned']} files, "
                f"{summary['verified']} OK, {summary['issues']} need attention, "
                f"{summary['cloud_only']} online-only"
            )

        if not folder_lines:
            folder_lines.append("- No files were found in the selected folder.")

        if result.integrity_issues:
            meaning = (
                "Meaning: at least one file is missing from the OneDrive match, has a different size, "
                "or could not be checked. Do not wipe this PC yet."
            )
        elif result.cloud_only_files:
            meaning = "Meaning: files exist in OneDrive but may not be downloaded locally. Review them before wiping."
        else:
            meaning = "Meaning: the checked files matched what the app expected in OneDrive."

        hash_text = "On" if result.hash_verification_enabled else "Off"
        return (
            "What was checked:\n"
            + "\n".join(folder_lines)
            + "\n\nPlain result:\n"
            + meaning
            + "\n\nTotals:\n"
            f"- Files checked: {result.scanned_files}\n"
            f"- Files OK: {result.verified_files}\n"
            f"- Need attention: {result.integrity_issues}\n"
            f"- Online-only: {result.cloud_only_files}\n"
            f"- Hash check: {hash_text}\n"
            f"- Risk: {result.risk_level}"
        )

    def cancel_scan(self) -> None:
        self.cancel_event.set()
        self.progress_text.set("Cancelling...")

    def view_details(self) -> None:
        if not self.current_result:
            messagebox.showinfo("No Results", "Run a scan first.")
            return
        DetailsWindow(self.root, self.current_result)

    def export_current_report(self) -> None:
        if not self.current_result:
            messagebox.showinfo("No Results", "Run a scan first.")
            return
        output_path = filedialog.asksaveasfilename(
            title="Export Report",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("CSV", "*.csv"), ("JSON", "*.json"), ("Text", "*.txt")],
        )
        if output_path:
            export_report(self.current_result, output_path)
            messagebox.showinfo("Export Complete", f"Report exported to:\n{output_path}")

    def run(self) -> None:
        self.root.mainloop()
