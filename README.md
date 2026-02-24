# Sync Integrity Verifier

Sync Integrity Verifier is a lightweight validation tool built for IT professionals to prevent accidental data loss during system wipes, OS upgrades, and device migrations — particularly in environments relying on OneDrive.

Rather than performing backups, the tool verifies that files are fully present, consistent, and safe before critical operations.

---

## When would you need this?

Use Sync Integrity Verifier when:

• Preparing a device for OS reinstallation  
• Upgrading from Windows 10 to Windows 11  
• Migrating user data to a new machine  
• Validating OneDrive sync completion  
• Verifying backups before formatting or wiping  
• Investigating suspected sync inconsistencies  

---

## What problem does it solve?

Cloud sync indicators can be misleading. Files and folders may appear present while their contents are incomplete, cloud-only, or improperly synced.

Sync Integrity Verifier performs a fast metadata-based comparison to detect:

• Missing files  
• Size mismatches  
• Cloud-only placeholders  
• Incomplete sync states  

---

## Core Objective

Provide IT staff with a clear, fast, and reliable answer to one critical question:

**"Is this device safe to wipe?"**
**"Are all file really backup?, are my files safe?"**

## What Is Actually Compared
- Source: the selected scan directory (`--scan <path>` or UI selected path).
- Target: OneDrive comparison root (enforced).
- The app detects local OneDrive and maps likely counterparts:
  - `Desktop` -> `OneDrive/Desktop`
  - `Documents` -> `OneDrive/Documents`
  - `Downloads` -> `OneDrive/Downloads`
  - otherwise fallback -> `OneDrive`

Per file, comparison checks are:
1. counterpart existence in target
2. size match
3. optional hash match (`--hash-verify`)

## OneDrive Preflight (Enforced)
Scans run only when OneDrive is both:
1. Configured for the current user (detectable root path).
2. Sync-available (Windows sync client process running).

If preflight fails, scan is blocked and a clear error is shown.

## MVP Features
- Right-click compatible scan command (`--scan <path>`)
- Full desktop app mode (`--ui`)
- Safe-to-wipe mode (`--safe-to-wipe`)
- Detection categories:
  - Cloud-only files (Windows attribute-based)
  - Missing counterpart in OneDrive target
  - Size mismatches
  - Optional hash mismatches
  - Incomplete/transient sync files
- Risk scoring:
  - `LOW`
  - `MEDIUM`
  - `HIGH`
- Exports:
  - `.pdf`
  - `.csv`
  - `.json`
  - `.txt`

## Run
Basic scan:
```bash
python3 -m app.main --scan "."
```

Optional hash verification:
```bash
python3 -m app.main --scan "." --hash-verify
```

Use explicit comparison root:
```bash
python3 -m app.main --scan "." --compare-root "~/OneDrive/Documents"
```

Launch desktop UI:
```bash
python3 -m app.main --ui
```

Safe-to-wipe scan:
```bash
python3 -m app.main --safe-to-wipe
```

## Context Menu (Windows)
Install for current user:
```powershell
py -m app.main --install-context-menu
```

Generate `.reg` file for deployment tooling:
```powershell
py -m app.main --generate-reg
```

Remove integration:
```powershell
py -m app.main --uninstall-context-menu
```

## Packaging Notes (Windows)
For installer delivery (`.msi` / `.exe`):
1. Package with `pyinstaller` or `cx_Freeze`.
2. Build installer with WiX / Inno Setup.
3. Register Start Menu shortcut, optional desktop shortcut, and run `--install-context-menu` post-install.
