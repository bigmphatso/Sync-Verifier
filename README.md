# Sync Integrity Verifier

[Download SyncVerifier.exe](dist/SyncVerifier.exe)

Sync Integrity Verifier is now a guided, step-by-step Windows application for checking whether important local folders are safely mirrored in OneDrive.

## Guided Flow

1. Identify OneDrive automatically.
   The app checks configured OneDrive accounts, environment variables, and sync client status with an animated loading state.

2. Choose what to compare.
   The default option checks the primary OneDrive folders: Desktop, Documents, and Pictures.

3. Pick a broader scope when needed.
   You can scan the entire user profile, the entire drive, or a custom folder.

4. Confirm and run.
   The app resolves the matching OneDrive comparison target, then runs the integrity check.

5. Review and export.
   Results show verified files, compared files, cloud-only files, integrity issues, risk level, and export options.

## Run

```powershell
py -m app.main --ui
```

## Build Executable

```powershell
.\build_exe.ps1
```

The built app is written to:

```text
dist\SyncVerifier.exe
```

The build script keeps PyInstaller's temporary work files outside the project folder. This avoids OneDrive locking intermediate files such as `build\SyncVerifier\localpycs` during rebuilds.
