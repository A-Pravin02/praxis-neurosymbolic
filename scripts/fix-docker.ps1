<#
.SYNOPSIS
    Clear the orphaned Docker Desktop sockets that stop the Linux engine starting.

.DESCRIPTION
    Symptom: `docker` commands fail with

        failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine

    and Docker Desktop shows "An unexpected error occurred". The backend log
    (%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log) carries the real
    cause:

        starting services: initializing Ingest server: listening on
        unix://.../Docker/run/sailor-ingest.sock: rename sailor-ingest.sock
        sailor-ingest.sock.stale: The file cannot be accessed by the system.

    Cause: %LOCALAPPDATA%\Docker\run holds AF_UNIX socket files, stored on
    Windows as reparse points. When Docker Desktop is killed rather than quit -
    a reboot, a Windows update, End Task, or the machine sleeping - the files
    survive but their owning process does not. Docker's next startup tries to
    rename each one to .stale before rebinding, Windows refuses (the reparse
    point resolves to nothing), and the whole engine start aborts. Docker cannot
    recover from this on its own, which is why it recurs until the files go.

    This script removes the dead sockets so Docker recreates them cleanly. It
    only ever touches socket files Docker owns; it does not touch images,
    containers, volumes or settings.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\fix-docker.ps1
    powershell -ExecutionPolicy Bypass -File scripts\fix-docker.ps1 -Start
#>
[CmdletBinding()]
param(
    # Launch Docker Desktop once the cleanup is done.
    [switch]$Start,
    # Wait this many seconds for the engine to answer before giving up.
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Ok($message)   { Write-Host "    $message" -ForegroundColor Green }
function Write-Warn($message) { Write-Host "    $message" -ForegroundColor Yellow }

# --- 1. stop everything holding the sockets open --------------------------
Write-Step 'Stopping Docker Desktop processes'
$names = @('Docker Desktop', 'com.docker.backend', 'com.docker.build',
           'com.docker.dev-envs', 'com.docker.extensions', 'dockerd', 'docker')
$stopped = 0
foreach ($name in $names) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $stopped++ } catch {}
    }
}
Write-Ok "stopped $stopped process(es)"

# The WSL VM keeps its own handles on these sockets. Without this the delete
# below succeeds and the next start fails again on a file Docker just recreated.
Write-Step 'Shutting down WSL'
& wsl.exe --shutdown 2>&1 | Out-Null
Write-Ok 'wsl --shutdown issued'

# --- 2. remove the orphaned sockets ---------------------------------------
$targets = @(
    (Join-Path $env:LOCALAPPDATA 'Docker\run'),
    (Join-Path $env:LOCALAPPDATA 'docker-secrets-engine')
)

foreach ($dir in $targets) {
    if (-not (Test-Path $dir)) { continue }
    Write-Step "Clearing $dir"

    $items = @(Get-ChildItem $dir -Force -ErrorAction SilentlyContinue)
    if (-not $items) { Write-Ok 'already empty'; continue }

    foreach ($item in $items) {
        try {
            Remove-Item $item.FullName -Force -Recurse -ErrorAction Stop
            Write-Ok "removed $($item.Name)"
        } catch {
            # A reparse point Windows refuses to delete in place can still be
            # moved out of the way with its parent, which is the reliable escape
            # hatch: Docker recreates the directory on next start.
            Write-Warn "could not remove $($item.Name) - renaming the directory instead"
            $backup = "$dir.broken-$(Get-Date -Format yyyyMMdd-HHmmss)"
            Move-Item $dir $backup -Force
            Write-Ok "moved to $backup (safe to delete later)"
            break
        }
    }
}

# --- 3. optionally bring it back up ---------------------------------------
if (-not $Start) {
    Write-Host ''
    Write-Host 'Cleanup done. Start Docker Desktop when ready.' -ForegroundColor Green
    return
}

Write-Step 'Starting Docker Desktop'
$exe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
if (-not (Test-Path $exe)) { $exe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" }
if (-not (Test-Path $exe)) { throw "Docker Desktop.exe not found - install path unknown" }
Start-Process $exe

Write-Step "Waiting up to ${TimeoutSeconds}s for the engine"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    # Not `docker version ... 2>$null`: redirecting a native command's stderr in
    # Windows PowerShell wraps each line in an ErrorRecord and prints a
    # NativeCommandError, so a still-starting engine looks like a script fault.
    # cmd swallows stderr before PowerShell ever sees it.
    $version = & cmd.exe /c 'docker version --format "{{.Server.Version}}" 2>nul'
    if ($LASTEXITCODE -eq 0 -and $version) {
        Write-Ok "engine ready (server $version)"
        return
    }
    Start-Sleep -Seconds 5
}

Write-Warn 'engine did not answer in time.'
Write-Warn 'Check %LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log for the current error.'
exit 1
