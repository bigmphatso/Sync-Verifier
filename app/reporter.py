from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .scanner import ScanResult


def _human_size(size: int | None) -> str:
    if size is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def result_to_text(result: ScanResult) -> str:
    comparison_target = result.comparison_target or "None"
    hash_mode = "ON" if result.hash_verification_enabled else "OFF"

    lines = [
        "SYNC INTEGRITY REPORT",
        f"Directory: {result.directory}",
        f"Comparison Target: {comparison_target}",
        f"Hash Verification: {hash_mode}",
        "",
        f"Scanned Files: {result.scanned_files}",
        f"Compared Files: {result.compared_files}",
        f"Hash Files Checked: {result.hash_files_checked}",
        f"Hash Mismatches: {result.hash_mismatches}",
        f"Verified Files: {result.verified_files}",
        f"Cloud-Only Files: {result.cloud_only_files}",
        f"Integrity Issues: {result.integrity_issues}",
        "",
        f"RISK LEVEL: {result.risk_level}",
        f"Recommendation: {result.recommendation}",
        "",
    ]

    if result.issues:
        lines.append("Issues:")
        for issue in result.issues:
            lines.append(
                f"- {issue.issue_type} | {issue.file_path} | local={_human_size(issue.local_size)} | cloud={_human_size(issue.cloud_size)}"
            )
    else:
        lines.append("No issues detected.")

    return "\n".join(lines)


def export_csv(result: ScanResult, output_file: str) -> None:
    path = Path(output_file)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "issue", "local_size", "cloud_size", "severity", "message"])
        for issue in result.issues:
            writer.writerow(
                [
                    issue.file_path,
                    issue.issue_type,
                    issue.local_size if issue.local_size is not None else "",
                    issue.cloud_size if issue.cloud_size is not None else "",
                    issue.severity,
                    issue.message,
                ]
            )


def export_json(result: ScanResult, output_file: str) -> None:
    path = Path(output_file)
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
        "duration_seconds": result.duration_seconds,
        "issues": [issue.__dict__ for issue in result.issues],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_lines(result: ScanResult) -> Iterable[str]:
    yield "SYNC INTEGRITY REPORT"
    yield f"Directory: {result.directory}"
    yield f"Comparison Target: {result.comparison_target or 'None'}"
    yield f"Hash Verification: {'ON' if result.hash_verification_enabled else 'OFF'}"
    yield f"Scanned Files: {result.scanned_files}"
    yield f"Compared Files: {result.compared_files}"
    yield f"Hash Files Checked: {result.hash_files_checked}"
    yield f"Hash Mismatches: {result.hash_mismatches}"
    yield f"Verified Files: {result.verified_files}"
    yield f"Cloud-Only Files: {result.cloud_only_files}"
    yield f"Integrity Issues: {result.integrity_issues}"
    yield f"RISK LEVEL: {result.risk_level}"
    yield f"Recommendation: {result.recommendation}"
    yield ""
    for issue in result.issues[:100]:
        yield (
            f"{issue.issue_type} | {issue.severity.upper()} | {issue.file_path} "
            f"(L:{_human_size(issue.local_size)} C:{_human_size(issue.cloud_size)})"
        )


def export_pdf(result: ScanResult, output_file: str) -> None:
    # Minimal PDF writer to avoid external dependencies.
    lines = list(_pdf_lines(result))
    text_commands = ["BT", "/F1 10 Tf", "50 780 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            text_commands.append("0 -14 Td")
        text_commands.append(f"({_escape_pdf_text(line)}) Tj")
    text_commands.append("ET")
    stream = "\n".join(text_commands).encode("utf-8")

    objects = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
    )
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(f"5 0 obj << /Length {len(stream)} >> stream\n".encode("utf-8") + stream + b"\nendstream endobj\n")

    header = b"%PDF-1.4\n"
    xref_positions = []
    content = bytearray(header)

    for obj in objects:
        xref_positions.append(len(content))
        content.extend(obj)

    xref_start = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("utf-8"))
    content.extend(b"0000000000 65535 f \n")
    for pos in xref_positions:
        content.extend(f"{pos:010d} 00000 n \n".encode("utf-8"))

    content.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("utf-8")
    )

    Path(output_file).write_bytes(content)


def export_report(result: ScanResult, output_file: str) -> None:
    suffix = Path(output_file).suffix.lower()
    if suffix == ".csv":
        export_csv(result, output_file)
    elif suffix == ".json":
        export_json(result, output_file)
    elif suffix == ".pdf":
        export_pdf(result, output_file)
    else:
        Path(output_file).write_text(result_to_text(result), encoding="utf-8")
