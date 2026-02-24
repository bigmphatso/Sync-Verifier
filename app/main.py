from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .reporter import export_report, result_to_text
from .scanner import (
    ScanResult,
    SyncScanner,
    check_onedrive_sync_status,
    resolve_onedrive_compare_root,
    safe_to_wipe_paths,
)


def install_context_menu() -> None:
    if os.name != "nt":
        print("Context menu install is only supported on Windows.")
        return

    import winreg

    command = f'"{sys.executable}" -m app.main --scan "%V" --show-report-window'
    keys = [
        r"Software\\Classes\\Directory\\shell\\Verify Sync Integrity",
        r"Software\\Classes\\Drive\\shell\\Verify Sync Integrity",
    ]

    for key_path in keys:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Verify Sync Integrity")
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, "shell32.dll,167")
        command_key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\\command")
        winreg.SetValueEx(command_key, "", 0, winreg.REG_SZ, command)

    print("Context menu installed for current user.")


def uninstall_context_menu() -> None:
    if os.name != "nt":
        print("Context menu uninstall is only supported on Windows.")
        return

    import winreg

    key_paths = [
        r"Software\\Classes\\Directory\\shell\\Verify Sync Integrity",
        r"Software\\Classes\\Directory\\shell\\Verify Sync Integrity\\command",
        r"Software\\Classes\\Drive\\shell\\Verify Sync Integrity",
        r"Software\\Classes\\Drive\\shell\\Verify Sync Integrity\\command",
    ]

    for key_path in reversed(key_paths):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except OSError:
            pass

    print("Context menu removed.")


def create_context_reg_file(output: str = "install_context_menu.reg") -> Path:
    command = f'"{sys.executable}" -m app.main --scan "%V" --show-report-window'
    content = f"""Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\\Software\\Classes\\Directory\\shell\\Verify Sync Integrity]
@="Verify Sync Integrity"
"Icon"="shell32.dll,167"

[HKEY_CURRENT_USER\\Software\\Classes\\Directory\\shell\\Verify Sync Integrity\\command]
@="{command}"

[HKEY_CURRENT_USER\\Software\\Classes\\Drive\\shell\\Verify Sync Integrity]
@="Verify Sync Integrity"
"Icon"="shell32.dll,167"

[HKEY_CURRENT_USER\\Software\\Classes\\Drive\\shell\\Verify Sync Integrity\\command]
@="{command}"
"""
    path = Path(output).resolve()
    path.write_text(content, encoding="utf-16")
    return path


def _resolve_compare_target(scan_path: str, compare_root: str | None) -> str | None:
    if compare_root:
        return str(Path(compare_root).expanduser().resolve())

    onedrive_status = check_onedrive_sync_status()
    if not onedrive_status.sync_available or not onedrive_status.root:
        return None

    scan_root = Path(scan_path).expanduser().resolve()
    return str(resolve_onedrive_compare_root(scan_root, onedrive_status.root))


def run_scan(path: str, compare_root: str | None = None, hash_verify: bool = False) -> ScanResult:
    scanner = SyncScanner()
    return scanner.scan(path, compare_root=compare_root, hash_verify=hash_verify)


def run_safe_to_wipe(compare_root: str | None = None, hash_verify: bool = False) -> ScanResult:
    scanner = SyncScanner()
    combined = ScanResult(
        directory="Safe To Wipe Scope",
        comparison_target=compare_root,
        hash_verification_enabled=hash_verify,
    )
    for folder in safe_to_wipe_paths():
        folder_compare = None
        if compare_root:
            compare_base = Path(compare_root)
            if folder.name in {"Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"}:
                folder_compare = str(compare_base / folder.name)
            elif folder.resolve() != compare_base.resolve():
                folder_compare = compare_root

        partial = scanner.scan(str(folder), compare_root=folder_compare, hash_verify=hash_verify)
        combined.scanned_files += partial.scanned_files
        combined.compared_files += partial.compared_files
        combined.hash_files_checked += partial.hash_files_checked
        combined.hash_mismatches += partial.hash_mismatches
        combined.verified_files += partial.verified_files
        combined.cloud_only_files += partial.cloud_only_files
        combined.integrity_issues += partial.integrity_issues
        combined.issues.extend(partial.issues)
    combined.risk_level, combined.recommendation = scanner._risk_profile(combined)
    return combined


def maybe_show_report_window(text: str) -> None:
    if os.name != "nt":
        print(text)
        return

    script = (
        "Add-Type -AssemblyName PresentationFramework; "
        f"[System.Windows.MessageBox]::Show(@'{text}'@, 'SYNC INTEGRITY REPORT')"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Integrity Verifier")
    parser.add_argument("--scan", help="Scan a specific directory")
    parser.add_argument("--safe-to-wipe", action="store_true", help="Run safe-to-wipe scan")
    parser.add_argument("--ui", action="store_true", help="Launch full application UI")
    parser.add_argument("--compare-root", help="Explicit comparison root path")
    parser.add_argument("--hash-verify", action="store_true", help="Enable optional content hash comparison")
    parser.add_argument("--export", help="Export report to path (.pdf, .csv, .json, .txt)")
    parser.add_argument("--json", action="store_true", help="Print result as JSON")
    parser.add_argument("--install-context-menu", action="store_true", help="Install right-click context menu")
    parser.add_argument("--uninstall-context-menu", action="store_true", help="Remove right-click context menu")
    parser.add_argument("--generate-reg", action="store_true", help="Generate .reg file for context menu")
    parser.add_argument("--show-report-window", action="store_true", help="Show popup report after CLI scan")

    args = parser.parse_args()

    if args.install_context_menu:
        install_context_menu()
        return 0

    if args.uninstall_context_menu:
        uninstall_context_menu()
        return 0

    if args.generate_reg:
        reg_path = create_context_reg_file()
        print(f"Generated: {reg_path}")
        return 0

    if args.ui or (not args.scan and not args.safe_to_wipe):
        from .ui import SyncVerifierApp

        app = SyncVerifierApp()
        app.run()
        return 0

    scan_path = args.scan if args.scan else str(Path.home())

    # Core behavior: scans are enforced as source-vs-OneDrive comparisons.
    onedrive_status = check_onedrive_sync_status()
    if args.compare_root is None and not onedrive_status.sync_available:
        print(f"OneDrive preflight failed: {onedrive_status.reason}")
        return 2

    if args.safe_to_wipe and args.compare_root is None:
        compare_target = str(onedrive_status.root) if onedrive_status.root else None
    else:
        compare_target = _resolve_compare_target(scan_path, args.compare_root)

    if compare_target is None:
        print("Unable to resolve OneDrive comparison target.")
        return 2

    result = (
        run_safe_to_wipe(compare_root=compare_target, hash_verify=args.hash_verify)
        if args.safe_to_wipe
        else run_scan(scan_path, compare_root=compare_target, hash_verify=args.hash_verify)
    )

    if args.export:
        export_report(result, args.export)

    if args.show_report_window:
        maybe_show_report_window(result_to_text(result))

    if args.json:
        payload = {
            "directory": result.directory,
            "comparison_target": result.comparison_target,
            "hash_verification_enabled": result.hash_verification_enabled,
            "scanned_files": result.scanned_files,
            "compared_files": result.compared_files,
            "hash_files_checked": result.hash_files_checked,
            "hash_mismatches": result.hash_mismatches,
            "verified_files": result.verified_files,
            "cloud_only_files": result.cloud_only_files,
            "integrity_issues": result.integrity_issues,
            "risk_level": result.risk_level,
            "recommendation": result.recommendation,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(result_to_text(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
