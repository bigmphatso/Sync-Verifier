from __future__ import annotations

import threading
import time
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox, ttk

from .reporter import export_report
from .scanner import (
    FileIssue,
    ScanResult,
    SyncScanner,
    check_onedrive_sync_status,
    resolve_onedrive_compare_root,
    safe_to_wipe_paths,
)


class DetailsWindow:
    def __init__(self, parent: Tk, result: ScanResult) -> None:
        self.result = result
        self.window = Toplevel(parent)
        self.window.title("Forensics Details")
        self.window.geometry("1100x500")

        self.show_only_problems = BooleanVar(value=True)
        self.show_cloud_only = BooleanVar(value=True)
        self.show_critical_only = BooleanVar(value=False)

        controls = ttk.Frame(self.window)
        controls.pack(fill="x", padx=10, pady=8)

        ttk.Checkbutton(controls, text="Show Only Problems", variable=self.show_only_problems, command=self.refresh).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(controls, text="Show Cloud-Only", variable=self.show_cloud_only, command=self.refresh).pack(
            side="left", padx=6
        )
        ttk.Checkbutton(controls, text="Show Critical Files", variable=self.show_critical_only, command=self.refresh).pack(
            side="left", padx=6
        )

        columns = ("file", "issue", "local", "cloud", "status")
        self.tree = ttk.Treeview(self.window, columns=columns, show="headings")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        self.tree.heading("file", text="File")
        self.tree.heading("issue", text="Issue")
        self.tree.heading("local", text="Local Size")
        self.tree.heading("cloud", text="Cloud Size")
        self.tree.heading("status", text="Status")

        self.tree.column("file", width=550)
        self.tree.column("issue", width=130)
        self.tree.column("local", width=100)
        self.tree.column("cloud", width=100)
        self.tree.column("status", width=100)

        self.refresh()

    def _display_size(self, size: int | None) -> str:
        if size is None:
            return "-"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024
        return str(size)

    def _allow_issue(self, issue: FileIssue) -> bool:
        if self.show_critical_only.get() and issue.severity != "critical":
            return False
        if not self.show_cloud_only.get() and issue.issue_type == "cloud_only":
            return False
        if self.show_only_problems.get():
            return True
        return True

    def refresh(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        for issue in self.result.issues:
            if not self._allow_issue(issue):
                continue
            status = "🚨" if issue.severity == "critical" else "⚠"
            self.tree.insert(
                "",
                "end",
                values=(
                    issue.file_path,
                    issue.issue_type,
                    self._display_size(issue.local_size),
                    self._display_size(issue.cloud_size),
                    status,
                ),
            )


class SyncVerifierApp:
    def __init__(self) -> None:
        self.scanner = SyncScanner()
        self.root = Tk()
        self.root.title("Sync Integrity Verifier")
        self.root.geometry("960x560")

        self.selected_directory = StringVar(value=str(Path.home()))
        self.status_text = StringVar(value="Status: Idle")
        self.last_scan_text = StringVar(value="Last Scan: Never")
        self.progress_text = StringVar(value="Files Checked: 0 | Issues Found: 0 | Speed: -")
        self.comparison_target_text = StringVar(value="Comparison Target: None")
        self.onedrive_status_text = StringVar(value="OneDrive Sync: Checking...")

        self.hash_verify = BooleanVar(value=False)

        self.current_result: ScanResult | None = None
        self.scan_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()

        self._build_ui()
        self._refresh_onedrive_status(silent=True)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x", pady=6)

        ttk.Entry(top, textvariable=self.selected_directory).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Select Directory", command=self.select_directory).pack(side="left", padx=4)
        ttk.Button(top, text="Quick Scan", command=lambda: self.start_scan("quick")).pack(side="left", padx=4)

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=4)
        ttk.Label(options, text="OneDrive comparison: ENFORCED").pack(side="left", padx=6)
        ttk.Checkbutton(options, text="Hash Verify (Optional)", variable=self.hash_verify).pack(side="left", padx=6)

        scope = ttk.LabelFrame(outer, text="Scan Scope", padding=10)
        scope.pack(fill="x", pady=8)

        ttk.Button(scope, text="Entire User Profile", command=self.scan_user_profile).pack(side="left", padx=4)
        ttk.Button(scope, text="Desktop / Documents / Downloads", command=self.scan_standard_folders).pack(
            side="left", padx=4
        )
        ttk.Button(scope, text="Custom Path", command=self.select_directory).pack(side="left", padx=4)
        ttk.Button(scope, text="Entire Drive", command=self.scan_entire_drive).pack(side="left", padx=4)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Run Integrity Check", command=lambda: self.start_scan("full")).pack(side="left", padx=4)
        ttk.Button(actions, text="Safe To Wipe Check", command=self.safe_to_wipe_check).pack(side="left", padx=4)
        ttk.Button(actions, text="Cancel", command=self.cancel_scan).pack(side="left", padx=4)

        status_frame = ttk.LabelFrame(outer, text="Scan Summary", padding=10)
        status_frame.pack(fill="x", pady=8)
        ttk.Label(status_frame, textvariable=self.last_scan_text).pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.status_text).pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.progress_text).pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.comparison_target_text).pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.onedrive_status_text).pack(anchor="w")

        self.progress = ttk.Progressbar(outer, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=8)

        self.report = ttk.Label(
            outer,
            text="SYNC INTEGRITY REPORT\n\nNo scans yet.",
            justify="left",
            anchor="w",
            padding=12,
            relief="groove",
        )
        self.report.pack(fill="both", expand=True, pady=8)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=4)
        ttk.Button(bottom, text="View Details", command=self.view_details).pack(side="left", padx=4)
        ttk.Button(bottom, text="Export Report", command=self.export_current_report).pack(side="left", padx=4)
        ttk.Button(bottom, text="Re-scan", command=lambda: self.start_scan("full")).pack(side="left", padx=4)

    def select_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.selected_directory.get())
        if selected:
            self.selected_directory.set(selected)

    def scan_user_profile(self) -> None:
        self.selected_directory.set(str(Path.home()))

    def scan_standard_folders(self) -> None:
        candidates = safe_to_wipe_paths()
        if candidates:
            self.selected_directory.set(str(candidates[0].parent))

    def scan_entire_drive(self) -> None:
        path = Path(self.selected_directory.get())
        anchor = path.anchor if path.anchor else str(path)
        self.selected_directory.set(anchor or str(path))

    def _resolve_compare_target(self, directory: str) -> str | None:
        status = check_onedrive_sync_status()
        if not status.sync_available or not status.root:
            return None

        scan_root = Path(directory).expanduser().resolve()
        return str(resolve_onedrive_compare_root(scan_root, status.root))

    def _refresh_onedrive_status(self, silent: bool) -> bool:
        status = check_onedrive_sync_status()
        if status.sync_available:
            root_text = str(status.root) if status.root else "Unknown"
            self.onedrive_status_text.set(f"OneDrive Sync: Ready ({root_text})")
            return True

        self.onedrive_status_text.set(f"OneDrive Sync: Not Ready ({status.reason})")
        if not silent:
            messagebox.showerror("OneDrive Not Ready", status.reason)
        return False

    def _preflight_onedrive(self) -> bool:
        return self._refresh_onedrive_status(silent=False)

    def cancel_scan(self) -> None:
        self.cancel_event.set()
        self.status_text.set("Status: Cancelling...")

    def start_scan(self, mode: str) -> None:
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan Running", "A scan is already in progress.")
            return

        directory = self.selected_directory.get().strip()
        if not directory:
            messagebox.showerror("Missing Directory", "Select a directory first.")
            return

        if not self._preflight_onedrive():
            return

        self.cancel_event.clear()
        self.status_text.set("Status: Scanning Files...")
        self.progress["value"] = 0

        compare_target = self._resolve_compare_target(directory)
        self.scan_thread = threading.Thread(
            target=self._run_scan,
            args=(directory, mode, compare_target, self.hash_verify.get()),
            daemon=True,
        )
        self.scan_thread.start()

    def _run_scan(self, directory: str, mode: str, compare_target: str | None, hash_verify: bool) -> None:
        started = time.time()
        issue_counter = {"count": 0}

        def progress_update(current: int, total: int) -> None:
            percent = (current / max(total, 1)) * 100
            elapsed = max(time.time() - started, 0.001)
            speed = int(current / elapsed)
            self.progress["value"] = percent
            self.progress_text.set(f"Files Checked: {current} | Issues Found: {issue_counter['count']} | Speed: {speed} files/s")
            self.root.update_idletasks()

        result = self.scanner.scan(
            directory,
            progress_callback=progress_update,
            cancel_event=self.cancel_event,
            compare_root=compare_target,
            hash_verify=hash_verify,
        )
        issue_counter["count"] = len(result.issues)

        if mode == "quick":
            result.recommendation = "Quick scan complete. Run full check for deeper validation."

        self.current_result = result
        self.last_scan_text.set(f"Last Scan: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.status_text.set("Status: Completed" if not self.cancel_event.is_set() else "Status: Cancelled")
        self.progress_text.set(
            f"Files Checked: {result.scanned_files} | Issues Found: {len(result.issues)} | Speed: complete"
        )
        self.comparison_target_text.set(f"Comparison Target: {result.comparison_target or 'None'}")

        report_text = (
            "SYNC INTEGRITY REPORT\n\n"
            f"Directory: {result.directory}\n"
            f"Comparison Target: {result.comparison_target or 'None'}\n"
            f"Hash Verification: {'ON' if result.hash_verification_enabled else 'OFF'}\n\n"
            f"✅ {result.verified_files} Files Verified\n"
            f"🔍 {result.compared_files} Files Compared\n"
            f"#️⃣ {result.hash_files_checked} Hashes Checked\n"
            f"⚠ {result.cloud_only_files} Cloud-Only Files\n"
            f"🚨 {result.integrity_issues} Integrity Issues\n\n"
            f"RISK LEVEL: {result.risk_level}\n"
            f"Recommendation: {result.recommendation}"
        )
        self.report.configure(text=report_text)

    def safe_to_wipe_check(self) -> None:
        paths = safe_to_wipe_paths()
        if not paths:
            messagebox.showwarning("No Standard Paths", "Could not find Desktop/Documents/Downloads to scan.")
            return

        if not self._preflight_onedrive():
            return

        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan Running", "A scan is already in progress.")
            return

        self.cancel_event.clear()
        self.status_text.set("Status: Running Safe To Wipe Check...")
        self.progress["value"] = 0

        def run_combined() -> None:
            aggregate = ScanResult(directory="Safe To Wipe Scope", hash_verification_enabled=self.hash_verify.get())
            aggregate.started_at = time.time()

            status = check_onedrive_sync_status()
            onedrive_root = status.root if status.sync_available else None

            for p in paths:
                if self.cancel_event.is_set():
                    break

                compare_target = None
                if onedrive_root:
                    compare_target = str(resolve_onedrive_compare_root(p.resolve(), onedrive_root))

                partial = self.scanner.scan(
                    str(p),
                    cancel_event=self.cancel_event,
                    compare_root=compare_target,
                    hash_verify=self.hash_verify.get(),
                )
                if partial.comparison_target and not aggregate.comparison_target:
                    aggregate.comparison_target = partial.comparison_target

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

            self.current_result = aggregate
            self.last_scan_text.set(f"Last Scan: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.status_text.set("Status: Completed")
            self.comparison_target_text.set(f"Comparison Target: {aggregate.comparison_target or 'None'}")

            if aggregate.risk_level == "LOW":
                status = "✅ SAFE TO WIPE"
            else:
                status = "🚨 NOT SAFE TO WIPE"

            details = (
                f"WIPE SAFETY STATUS\n\n{status}\n\n"
                f"Compared Files: {aggregate.compared_files}\n"
                f"Hash Checks: {aggregate.hash_files_checked}\n"
                f"Hash Mismatches: {aggregate.hash_mismatches}\n"
                f"Detected Issues:\n"
                f"• {aggregate.integrity_issues} Files Not Fully Synced\n"
                f"• {aggregate.cloud_only_files} Cloud-Only Files\n\n"
                f"Recommendation:\n{aggregate.recommendation}"
            )
            self.report.configure(text=details)

        self.scan_thread = threading.Thread(target=run_combined, daemon=True)
        self.scan_thread.start()

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
        if not output_path:
            return

        try:
            export_report(self.current_result, output_path)
            messagebox.showinfo("Export Complete", f"Report exported to:\n{output_path}")
        except OSError as exc:
            messagebox.showerror("Export Failed", str(exc))

    def run(self) -> None:
        self.root.mainloop()
