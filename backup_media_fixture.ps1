<#
.SYNOPSIS
    Backup only the media volume and optional Django fixture.

.DESCRIPTION
    Creates a timestamped archive with:
      - constructor_media_volume compressed to media_*.tar.gz
      - optional Django JSON fixture (without contenttypes/auth.permission)

    The folder is saved under C:\constructor\backup\media_fixture_YYYYMMDD_HHmm.

.NOTES
    Run from PowerShell:
        powershell.exe -ExecutionPolicy Bypass -File C:\constructor\backup_media_fixture.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipFixture
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message, [ConsoleColor]$Color = 'Gray')
    $timestamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
}

$projectDir = "C:\constructor"
$backupRoot = "C:\constructor\backup"
$timestamp  = Get-Date -Format 'yyyyMMdd_HHmm'
$backupDir  = Join-Path $backupRoot "media_fixture_$timestamp"

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$backupDirFull = (Resolve-Path $backupDir).Path

Write-Log "Project dir: $projectDir"
Write-Log "Backup dir : $backupDirFull"

Write-Log "Checking Docker..." 'Cyan'
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not reachable. Start Docker Desktop and retry."
}
Write-Log "Docker is available." 'Green'

$mediaArchive = Join-Path $backupDirFull "media_$timestamp.tar.gz"
Write-Log "Archiving media volume -> $mediaArchive" 'Cyan'

docker run --rm `
    -v constructor_media_volume:/data `
    -v "${backupDirFull}:/backup" `
    alpine sh -c "cd /data && tar czf /backup/media_$timestamp.tar.gz ."

Write-Log "Media archive created." 'Green'

if (-not $SkipFixture) {
    $fixturePath = Join-Path $backupDirFull "fixture_$timestamp.json"
    Write-Log "Dumping Django fixture -> $fixturePath" 'Cyan'

    $fixtureCommand = "cd /app/taskmanager && PYTHONIOENCODING=utf-8 python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2"
    $fixtureContent = docker compose --project-directory $projectDir exec -T web bash -lc $fixtureCommand

    [System.IO.File]::WriteAllText(
        $fixturePath,
        $fixtureContent,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Log "Fixture saved." 'Green'
} else {
    Write-Log "Fixture dump skipped." 'Yellow'
}

Write-Log "Backup ready: $backupDirFull" 'Yellow'
Get-ChildItem $backupDirFull | ForEach-Object {
    Write-Host ("  - {0}`t{1:N0} bytes" -f $_.Name, $_.Length)
}
