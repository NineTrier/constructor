<#
.SYNOPSIS
    Restore media archive and optional Django fixture produced by backup_media_fixture.ps1.

.DESCRIPTION
    Finds the latest media_fixture_* directory (or uses the provided name) under
    C:\constructor\backup and restores:
      - media_*.tar.gz into constructor_media_volume
      - fixture_*.json via manage.py loaddata (optional)

.NOTES
    Run from PowerShell:
        powershell.exe -ExecutionPolicy Bypass -File .\restore_media_fixture.ps1
#>

[CmdletBinding()]
param(
    [string]$ProjectDir = "C:\constructor",
    [string]$BackupRoot = "C:\constructor\backup",
    [string]$BackupName,
    [switch]$LoadFixture,
    [switch]$SkipMedia
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [ConsoleColor]$Color = 'Gray')
    $timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
}

function Convert-FixtureToUtf8 {
    param([string]$SourcePath)

    $bytes = [System.IO.File]::ReadAllBytes($SourcePath)
    if ($bytes.Length -eq 0) {
        return $SourcePath
    }

    $utf8Encoding = New-Object System.Text.UTF8Encoding($false)
    $text = $null

    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        # Already UTF-8 with BOM
        return $SourcePath
    } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        # UTF-16 LE
        $text = [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
    } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
        # UTF-16 BE
        $text = [System.Text.Encoding]::BigEndianUnicode.GetString($bytes, 2, $bytes.Length - 2)
    } else {
        # Assume UTF-8 (without BOM) or ASCII
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        return $SourcePath
    }

    $tempPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetFileName($SourcePath))
    [System.IO.File]::WriteAllText($tempPath, $text, $utf8Encoding)
    return $tempPath
}

if (-not (Test-Path $BackupRoot)) {
    throw "Backup directory '$BackupRoot' not found."
}

if (-not (Test-Path $ProjectDir)) {
    throw "Project directory '$ProjectDir' not found."
}

$projectDirFull = (Resolve-Path $ProjectDir).Path
$backupRootFull = (Resolve-Path $BackupRoot).Path

Write-Log "Project dir : $projectDirFull"
Write-Log "Backup root : $backupRootFull"

$backups = Get-ChildItem -Path $backupRootFull -Directory |
    Where-Object { $_.Name -like 'media_fixture_*' } |
    Sort-Object LastWriteTime -Descending

if (-not $backups) {
    throw "No media_fixture_* folders found under '$backupRootFull'."
}

if ($BackupName) {
    $backupDir = Join-Path $backupRootFull $BackupName
    if (-not (Test-Path $backupDir)) {
        throw "Specified backup '$BackupName' does not exist."
    }
    $backupInfo = Get-Item $backupDir
} else {
    $backupInfo = $backups[0]
    $backupDir = $backupInfo.FullName
}

Write-Log "Using backup: $($backupInfo.Name)" 'Cyan'

$mediaArchive = Get-ChildItem -Path $backupDir -Filter 'media_*.tar.gz' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1

if (-not $mediaArchive -and -not $SkipMedia.IsPresent) {
    throw "Media archive (media_*.tar.gz) not found in backup. Pass -SkipMedia to continue without it."
}

$fixtureFile = Get-ChildItem -Path $backupDir -Filter 'fixture_*.json' -File |
    Sort-Object Name -Descending |
    Select-Object -First 1

Write-Log "Checking Docker..." 'Cyan'
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop and retry."
}
Write-Log "Docker is available." 'Green'

# Quick connectivity check
Write-Log "Checking web container status..." 'Cyan'
& docker compose --project-directory $projectDirFull ps web | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Web container is not running. Start the stack before restoring media/fixture."
}

if ($mediaArchive -and -not $SkipMedia.IsPresent) {
    $mediaMountArg = ('{0}:/backup' -f $backupDir)
    $mediaCommand = "rm -rf /data/* && tar xzf /backup/$($mediaArchive.Name) -C /data"
    Write-Log "Restoring media volume from $($mediaArchive.Name)..." 'Cyan'
    & docker run --rm -v constructor_media_volume:/data -v $mediaMountArg alpine sh -c $mediaCommand | Out-Null
    Write-Log "Media restored." 'Green'
} else {
    Write-Log "Media restore skipped." 'Yellow'
}

if ($LoadFixture.IsPresent) {
    if (-not $fixtureFile) {
        Write-Log "fixture_*.json not found in backup; skipping loaddata." 'Yellow'
    } else {
        $fixtureDest = "/tmp/$($fixtureFile.Name)"
        $fixtureSourcePath = $fixtureFile.FullName
        $convertedFixture = Convert-FixtureToUtf8 -SourcePath $fixtureSourcePath
        Write-Log "Loading Django fixture $($fixtureFile.Name)..." 'Cyan'
        & docker compose --project-directory $projectDirFull cp $convertedFixture "web:$fixtureDest" | Out-Null
        $fixtureCommand = "cd /app/taskmanager && PYTHONIOENCODING=utf-8 python manage.py loaddata $fixtureDest"
        & docker compose --project-directory $projectDirFull exec web bash -lc $fixtureCommand | Out-Null
        & docker compose --project-directory $projectDirFull exec web rm -f $fixtureDest | Out-Null
        if ($convertedFixture -ne $fixtureSourcePath -and (Test-Path $convertedFixture)) {
            Remove-Item $convertedFixture -Force
        }
        Write-Log "Fixture loaded." 'Green'
    }
} else {
    Write-Log "Fixture load skipped." 'Yellow'
}

Write-Log "Restore complete." 'Yellow'
