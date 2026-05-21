from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CACHE_FILE = Path.home() / ".sync_integrity_verifier_cache.json"

FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_PINNED = 0x00080000
FILE_ATTRIBUTE_UNPINNED = 0x00100000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

PRIMARY_ONEDRIVE_FOLDERS = ("Desktop", "Documents", "Pictures")
COMMON_SCAN_FOLDERS = PRIMARY_ONEDRIVE_FOLDERS + ("Downloads", "Music", "Videos")


@dataclass
class FileIssue:
    file_path: str
    issue_type: str
    local_size: int | None
    cloud_size: int | None
    severity: str
    message: str


@dataclass
class ScanResult:
    directory: str
    comparison_target: str | None = None
    hash_verification_enabled: bool = False
    scanned_files: int = 0
    compared_files: int = 0
    hash_files_checked: int = 0
    hash_mismatches: int = 0
    verified_files: int = 0
    cloud_only_files: int = 0
    integrity_issues: int = 0
    risk_level: str = "LOW"
    recommendation: str = "No integrity risks detected"
    issues: list[FileIssue] = field(default_factory=list)
    folder_summaries: list[dict[str, str | int]] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return max(self.finished_at - self.started_at, 0.0)
        return 0.0


@dataclass
class OneDriveStatus:
    configured: bool
    sync_available: bool
    root: Path | None
    reason: str


@dataclass
class InspectOutcome:
    issues: list[FileIssue] = field(default_factory=list)
    compared: bool = False
    hash_checked: bool = False


class SyncScanner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, dict[str, float]]:
        if not CACHE_FILE.exists():
            return {}
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self) -> None:
        try:
            CACHE_FILE.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        except OSError:
            pass

    def scan(
        self,
        root_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
        compare_root: str | None = None,
        hash_verify: bool = False,
        hash_algorithm: str = "sha256",
    ) -> ScanResult:
        root = Path(root_path).expanduser().resolve()
        comparison_target = Path(compare_root).expanduser().resolve() if compare_root else None

        result = ScanResult(
            directory=str(root),
            comparison_target=str(comparison_target) if comparison_target else None,
            hash_verification_enabled=hash_verify,
            started_at=time.time(),
        )

        file_paths = self._collect_files(root, cancel_event)
        total_files = len(file_paths)
        if total_files == 0:
            result.finished_at = time.time()
            return result

        workers = min(32, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(self._inspect_file, path, root, comparison_target, hash_verify, hash_algorithm)
                for path in file_paths
            ]
            for index, future in enumerate(futures, start=1):
                if cancel_event and cancel_event.is_set():
                    break
                outcome = future.result()
                result.scanned_files += 1
                result.compared_files += int(outcome.compared)
                result.hash_files_checked += int(outcome.hash_checked)

                for issue in outcome.issues:
                    result.issues.append(issue)
                    if issue.issue_type == "cloud_only":
                        result.cloud_only_files += 1
                    else:
                        result.integrity_issues += 1
                    if issue.issue_type == "onedrive_hash_mismatch":
                        result.hash_mismatches += 1

                if progress_callback:
                    progress_callback(index, total_files)

        result.verified_files = max(result.scanned_files - len(result.issues), 0)
        result.risk_level, result.recommendation = self._risk_profile(result)
        result.finished_at = time.time()
        self._save_cache()
        return result

    def _collect_files(self, root: Path, cancel_event: threading.Event | None) -> list[Path]:
        files: list[Path] = []
        for current_root, dirs, filenames in os.walk(root, topdown=True):
            dirs[:] = [d for d in dirs if d not in {"$RECYCLE.BIN", "System Volume Information", ".git"}]
            if cancel_event and cancel_event.is_set():
                break
            files.extend(Path(current_root) / name for name in filenames)
        return files

    def _win_attributes(self, path: Path) -> int | None:
        if os.name != "nt":
            return None
        import ctypes

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF:
            return None
        return int(attrs)

    def _inspect_file(
        self,
        file_path: Path,
        scan_root: Path,
        comparison_target: Path | None,
        hash_verify: bool,
        hash_algorithm: str,
    ) -> InspectOutcome:
        outcome = InspectOutcome()

        try:
            stat = file_path.stat()
        except OSError:
            outcome.issues.append(
                FileIssue(str(file_path), "missing", None, None, "critical", "File was inaccessible during scan")
            )
            return outcome

        attrs = self._win_attributes(file_path)
        if self._is_cloud_only(attrs):
            outcome.issues.append(
                FileIssue(
                    str(file_path),
                    "cloud_only",
                    stat.st_size,
                    None,
                    "warning",
                    "File is cloud-only and not guaranteed local for wipe",
                )
            )

        with self._lock:
            cached = self._cache.get(str(file_path), {})
            previous_size = int(cached.get("size", stat.st_size))
            previous_mtime = float(cached.get("mtime", stat.st_mtime))
            self._cache[str(file_path)] = {"size": stat.st_size, "mtime": stat.st_mtime}

        if attrs is not None and self._has_cloud_marker(attrs):
            if stat.st_size < previous_size and stat.st_mtime >= previous_mtime:
                outcome.issues.append(
                    FileIssue(
                        str(file_path),
                        "size_mismatch",
                        stat.st_size,
                        previous_size,
                        "critical",
                        "Local size shrank compared to previous synced metadata snapshot",
                    )
                )

        if self._looks_incomplete_sync(file_path, stat.st_size, attrs):
            outcome.issues.append(
                FileIssue(
                    str(file_path),
                    "incomplete_sync",
                    stat.st_size,
                    None,
                    "critical",
                    "File appears partially synced or transient",
                )
            )

        if comparison_target:
            self._compare_against_target(
                outcome, file_path, scan_root, stat.st_size, comparison_target, hash_verify, hash_algorithm
            )

        return outcome

    def _compare_against_target(
        self,
        outcome: InspectOutcome,
        file_path: Path,
        scan_root: Path,
        local_size: int,
        comparison_target: Path,
        hash_verify: bool,
        hash_algorithm: str,
    ) -> None:
        try:
            relative = file_path.relative_to(scan_root)
        except ValueError:
            return

        target_file = comparison_target / relative
        outcome.compared = True

        if not target_file.exists():
            outcome.issues.append(
                FileIssue(
                    str(file_path),
                    "onedrive_missing",
                    local_size,
                    None,
                    "critical",
                    "No matching file found in OneDrive comparison target",
                )
            )
            return

        try:
            target_stat = target_file.stat()
        except OSError:
            outcome.issues.append(
                FileIssue(
                    str(file_path),
                    "onedrive_inaccessible",
                    local_size,
                    None,
                    "critical",
                    "Matching file exists in comparison target but could not be read",
                )
            )
            return

        if local_size != target_stat.st_size:
            outcome.issues.append(
                FileIssue(
                    str(file_path),
                    "onedrive_size_mismatch",
                    local_size,
                    target_stat.st_size,
                    "critical",
                    "Size mismatch between selected path and OneDrive target",
                )
            )
            return

        if hash_verify:
            local_hash = self._hash_file(file_path, hash_algorithm)
            target_hash = self._hash_file(target_file, hash_algorithm)
            outcome.hash_checked = True
            if local_hash is None or target_hash is None:
                outcome.issues.append(
                    FileIssue(
                        str(file_path),
                        "hash_unavailable",
                        local_size,
                        target_stat.st_size,
                        "warning",
                        "Hash verification skipped because one side could not be read",
                    )
                )
                return
            if local_hash != target_hash:
                outcome.issues.append(
                    FileIssue(
                        str(file_path),
                        "onedrive_hash_mismatch",
                        local_size,
                        target_stat.st_size,
                        "critical",
                        "Content hash mismatch between selected path and OneDrive target",
                    )
                )

    def _hash_file(self, path: Path, algorithm: str) -> str | None:
        try:
            digest = hashlib.new(algorithm)
        except ValueError:
            digest = hashlib.sha256()

        try:
            with path.open("rb") as file_handle:
                while chunk := file_handle.read(1024 * 1024):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    def _is_cloud_only(self, attrs: int | None) -> bool:
        if attrs is None:
            return False
        offline = bool(attrs & FILE_ATTRIBUTE_OFFLINE)
        unpinned = bool(attrs & FILE_ATTRIBUTE_UNPINNED)
        pinned = bool(attrs & FILE_ATTRIBUTE_PINNED)
        recall_only = bool(attrs & FILE_ATTRIBUTE_RECALL_ON_OPEN)
        return (offline and unpinned and not pinned) or recall_only

    def _has_cloud_marker(self, attrs: int) -> bool:
        return bool(
            attrs
            & (
                FILE_ATTRIBUTE_OFFLINE
                | FILE_ATTRIBUTE_PINNED
                | FILE_ATTRIBUTE_UNPINNED
                | FILE_ATTRIBUTE_RECALL_ON_OPEN
                | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
            )
        )

    def _looks_incomplete_sync(self, file_path: Path, size: int, attrs: int | None) -> bool:
        if file_path.suffix.lower() in {".tmp", ".partial", ".download", ".crdownload"}:
            return True
        if size == 0 and file_path.suffix.lower() in {".docx", ".xlsx", ".pptx", ".pdf", ".pst", ".zip"}:
            return attrs is not None and self._has_cloud_marker(attrs)
        return False

    def _risk_profile(self, result: ScanResult) -> tuple[str, str]:
        critical = sum(1 for issue in result.issues if issue.severity == "critical")
        warnings = sum(1 for issue in result.issues if issue.severity == "warning")
        if critical:
            return "HIGH", "Resolve sync integrity issues before wipe."
        if warnings:
            return "MEDIUM", "Review cloud-only files before wipe."
        return "LOW", "No integrity risks detected."


def detect_onedrive_root() -> Path | None:
    candidates: list[Path] = []

    for env_name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "ONEDRIVE"):
        if value := os.environ.get(env_name):
            candidates.append(Path(value).expanduser())

    candidates.extend(_onedrive_roots_from_registry())
    candidates.append(Path.home() / "OneDrive")
    candidates.extend(path for path in Path.home().parent.glob("OneDrive*") if path.is_dir())

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _onedrive_roots_from_registry() -> list[Path]:
    if os.name != "nt":
        return []

    try:
        import winreg
    except ImportError:
        return []

    roots: list[Path] = []
    base = r"Software\Microsoft\OneDrive\Accounts"
    account_names = ("Personal", "Business1", "Business2", "Business3", "Business4")

    for account in account_names:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{account}") as key:
                user_folder, _ = winreg.QueryValueEx(key, "UserFolder")
                if user_folder:
                    roots.append(Path(str(user_folder)).expanduser())
        except OSError:
            continue
    return roots


def _is_onedrive_process_running() -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OneDrive.exe"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "onedrive.exe" in completed.stdout.lower()


def check_onedrive_sync_status() -> OneDriveStatus:
    root = detect_onedrive_root()
    if not root:
        return OneDriveStatus(False, False, None, "OneDrive is not configured for this user profile.")
    if not root.exists() or not root.is_dir():
        return OneDriveStatus(True, False, root, "OneDrive root path is configured but not accessible.")
    if os.name != "nt":
        return OneDriveStatus(True, True, root, "OneDrive folder detected.")

    if _is_onedrive_process_running():
        return OneDriveStatus(True, True, root, "OneDrive folder detected and sync client appears to be running.")

    return OneDriveStatus(
        True,
        True,
        root,
        "OneDrive folder detected. Sync client process was not confirmed, so check OneDrive status before wiping.",
    )


def resolve_onedrive_compare_root(scan_root: Path, onedrive_root: Path) -> Path:
    scan_root = scan_root.expanduser().resolve()
    onedrive_root = onedrive_root.expanduser().resolve()

    if scan_root == onedrive_root:
        return onedrive_root

    try:
        scan_root.relative_to(onedrive_root)
        if scan_root.name in COMMON_SCAN_FOLDERS:
            return scan_root
        return onedrive_root
    except ValueError:
        pass

    if scan_root.name in COMMON_SCAN_FOLDERS:
        return onedrive_root / scan_root.name
    if scan_root.parent == Path.home():
        return onedrive_root / scan_root.name
    return onedrive_root


def _windows_known_folder_paths() -> dict[str, Path]:
    if os.name != "nt":
        return {}

    try:
        import winreg
    except ImportError:
        return {}

    value_names = {
        "Desktop": "Desktop",
        "Documents": "Personal",
        "Pictures": "My Pictures",
    }
    paths: dict[str, Path] = {}
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            for label, value_name in value_names.items():
                try:
                    raw_value, _ = winreg.QueryValueEx(key, value_name)
                except OSError:
                    continue
                expanded = os.path.expandvars(str(raw_value))
                path = Path(expanded).expanduser()
                if path.exists() and path.is_dir():
                    paths[label] = path.resolve()
    except OSError:
        return paths

    return paths


def default_profile_paths() -> list[Path]:
    home = Path.home()
    known_folders = _windows_known_folder_paths()
    one_drive = detect_onedrive_root()
    paths: list[Path] = []

    for name in PRIMARY_ONEDRIVE_FOLDERS:
        candidates = [
            known_folders.get(name),
            home / name,
            one_drive / name if one_drive else None,
        ]
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_dir():
                resolved = candidate.resolve()
                if resolved not in paths:
                    paths.append(resolved)
                break

    return paths


def safe_to_wipe_paths() -> list[Path]:
    paths = default_profile_paths()
    if one_drive := detect_onedrive_root():
        paths.append(one_drive)
    return paths
