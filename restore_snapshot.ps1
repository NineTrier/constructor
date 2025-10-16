<#
.SYNOPSIS
    Restore the most recent snapshot produced by take_snapshot.ps1.

.DESCRIPTION
    Stops the current Compose stack (optionally removing volumes), loads the saved
    constructor-web image, brings containers back online, restores the PostgreSQL
    dump, unpacks the media volume archive, and optionally applies the JSON fixture.

.NOTES
    Run from an elevated PowerShell prompt if Docker requires it.
    Example:
        powershell.exe -ExecutionPolicy Bypass -File .\restore_snapshot.ps1
#>

[CmdletBinding()]
param(
    [string]$ProjectDir = "C:\constructor",
    [string]$BackupRoot = "C:\constructor\backup",
    [string]$SnapshotName,
    [switch]$LoadFixture,
    [switch]$RecreateVolumes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log {
    param(
        [string]$Message,
        [ConsoleColor]$Color = 'Gray'
    )
    $timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
}

if (-not (Test-Path $BackupRoot)) {
    throw "Backup directory '$BackupRoot' not found."
}

if (-not (Test-Path $ProjectDir)) {
    Write-Log "Project directory '$ProjectDir' not found. Creating..." 'Yellow'
    New-Item -ItemType Directory -Path $ProjectDir | Out-Null
}

# Resolve directories to avoid relative paths later
$projectDirFull = (Resolve-Path $ProjectDir).Path
$backupRootFull = (Resolve-Path $BackupRoot).Path

Write-Log "Project dir : $projectDirFull"
Write-Log "Backup root : $backupRootFull"

# Determine which snapshot to restore
$snapshots = Get-ChildItem -Path $backupRootFull -Directory |
    Where-Object { $_.Name -like 'snapshot_*' } |
    Sort-Object LastWriteTime -Descending

if (-not $snapshots) {
    throw "No snapshot_* folders found under '$backupRootFull'."
}

if ($SnapshotName) {
    $snapshotDir = Join-Path $backupRootFull $SnapshotName
    if (-not (Test-Path $snapshotDir)) {
        throw "Specified snapshot '$SnapshotName' does not exist."
    }
    $snapshotInfo = Get-Item $snapshotDir
} else {
    $snapshotInfo = $snapshots[0]
    $snapshotDir = $snapshotInfo.FullName
}

Write-Log "Using snapshot: $($snapshotInfo.Name)" 'Cyan'

# Ensure docker-compose.yml and .env are available
$composeTarget = Join-Path $projectDirFull 'docker-compose.yml'
$composeSource = Join-Path $snapshotDir 'docker-compose.yml'
if (-not (Test-Path $composeTarget)) {
    if (Test-Path $composeSource) {
        Write-Log "Copying docker-compose.yml from snapshot." 'Yellow'
        Copy-Item -Path $composeSource -Destination $composeTarget -Force
    } else {
        throw "docker-compose.yml not found in project or snapshot."
    }
}

$envTarget = Join-Path $projectDirFull '.env'
$envSource = Join-Path $snapshotDir '.env'
if (-not (Test-Path $envTarget) -and (Test-Path $envSource)) {
    Write-Log "Copying .env from snapshot." 'Yellow'
    Copy-Item -Path $envSource -Destination $envTarget -Force
}

# Locate snapshot artefacts
$pgDump = Get-ChildItem -Path $snapshotDir -Filter 'postgres_*.sqlc' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $pgDump) {
    throw "PostgreSQL dump (postgres_*.sqlc) not found in snapshot."
}

$mediaArchive = Get-ChildItem -Path $snapshotDir -Filter 'media_*.tar.gz' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $mediaArchive) {
    throw "Media archive (media_*.tar.gz) not found in snapshot."
}

$imageTar = Get-ChildItem -Path $snapshotDir -Filter 'constructor-web_*.tar' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $imageTar) {
    throw "constructor-web image archive (constructor-web_*.tar) not found in snapshot."
}

$fixtureFile = Get-ChildItem -Path $snapshotDir -Filter 'fixture_*.json' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1

# Docker availability check
Write-Log "Checking Docker..." 'Cyan'
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop and retry."
}
Write-Log "Docker is available." 'Green'

# Stop current stack
$downArgs = @("compose","--project-directory",$projectDirFull,"down")
if ($RecreateVolumes.IsPresent) {
    $downArgs += "--volumes"
    Write-Log "Stopping stack and removing project volumes..." 'Cyan'
} else {
    Write-Log "Stopping stack (volumes preserved)..." 'Cyan'
}
& docker @downArgs | Out-Null

# Load saved image
Write-Log "Loading constructor-web image from $($imageTar.Name)..." 'Cyan'
& docker image load -i $imageTar.FullName | Out-Null
Write-Log "Image loaded." 'Green'

# Start stack
Write-Log "Starting containers..." 'Cyan'
& docker compose --project-directory $projectDirFull up -d | Out-Null

# Wait for Postgres readiness
Write-Log "Waiting for PostgreSQL to accept connections..." 'Cyan'
$maxAttempts = 30
$ready = $false
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    & docker compose --project-directory $projectDirFull exec db pg_isready -U constructor | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    throw "PostgreSQL did not become ready within $($maxAttempts * 2) seconds."
}
Write-Log "PostgreSQL is ready." 'Green'

# Restore PostgreSQL dump
Write-Log "Restoring database from $($pgDump.Name)..." 'Cyan'
& docker compose --project-directory $projectDirFull cp $pgDump.FullName "db:/tmp/restore.sqlc" | Out-Null
& docker compose --project-directory $projectDirFull exec db bash -lc "pg_restore -U constructor --clean --if-exists -d constructor /tmp/restore.sqlc" | Out-Null
& docker compose --project-directory $projectDirFull exec db rm -f /tmp/restore.sqlc | Out-Null
Write-Log "Database restored." 'Green'

# Restore media volume
$mediaMountArg = ('{0}:/backup' -f $snapshotDir)
$mediaCommand = "rm -rf /data/* && tar xzf /backup/$($mediaArchive.Name) -C /data"
Write-Log "Restoring media volume from $($mediaArchive.Name)..." 'Cyan'
& docker run --rm -v constructor_media_volume:/data -v $mediaMountArg alpine sh -c $mediaCommand | Out-Null
Write-Log "Media restored." 'Green'

# Optionally load fixture
if ($LoadFixture.IsPresent) {
    if (-not $fixtureFile) {
        Write-Log "No fixture_*.json found in snapshot; skipping loaddata." 'Yellow'
    } else {
        $fixtureDest = "/tmp/$($fixtureFile.Name)"
        Write-Log "Loading Django fixture $($fixtureFile.Name)..." 'Cyan'
        & docker compose --project-directory $projectDirFull cp $fixtureFile.FullName "web:$fixtureDest" | Out-Null
        $fixtureCommand = "cd /app/taskmanager && PYTHONIOENCODING=utf-8 python manage.py loaddata $fixtureDest"
        & docker compose --project-directory $projectDirFull exec web bash -lc $fixtureCommand | Out-Null
        & docker compose --project-directory $projectDirFull exec web rm -f $fixtureDest | Out-Null
        Write-Log "Fixture loaded." 'Green'
    }
}

Write-Log "Restore complete. Visit your app to verify everything looks correct." 'Yellow'
