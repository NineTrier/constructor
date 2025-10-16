<#
.SYNOPSIS
  Полный снапшот проекта: pg_dump, архив media тома, фикстура (опц.), docker image save.

.NOTES
  Запуск:
    powershell.exe -ExecutionPolicy Bypass -File C:\constructor\take_snapshot.ps1
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

# --- статические пути ---
$projectDir = "C:\constructor"
$backupRoot = "C:\constructor\backup"
$timestamp  = Get-Date -Format 'yyyyMMdd_HHmm'
$backupDir  = Join-Path $backupRoot "snapshot_$timestamp"

New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$backupDirFull = (Resolve-Path $backupDir).Path

Write-Log "Project dir: $projectDir"
Write-Log "Backup dir : $backupDirFull"

# --- проверка Docker ---
Write-Log "Проверка Docker..." 'Cyan'
docker info | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon недоступен. Запустите Docker Desktop и повторите."
}
Write-Log "Docker доступен." 'Green'

# --- дамп Postgres ---
$pgDumpPath = Join-Path $backupDirFull "postgres_$timestamp.sqlc"
Write-Log "Дамп Postgres -> $pgDumpPath" 'Cyan'

docker compose --project-directory $projectDir exec -T db bash -lc "pg_dump -U constructor -F c constructor > /tmp/pg_dump.sqlc"
docker compose --project-directory $projectDir cp db:/tmp/pg_dump.sqlc $pgDumpPath
docker compose --project-directory $projectDir exec -T db rm -f /tmp/pg_dump.sqlc

Write-Log "Дамп Postgres завершён." 'Green'

# --- архив медиаволума ---
$mediaArchive = Join-Path $backupDirFull "media_$timestamp.tar.gz"
Write-Log "Архив медиаволума -> $mediaArchive" 'Cyan'

docker run --rm `
    -v constructor_media_volume:/data `
    -v "${backupDirFull}:/backup" `
    alpine sh -c "cd /data && tar czf /backup/media_$timestamp.tar.gz ."

Write-Log "Архив медиаволума готов." 'Green'

# --- (опционально) JSON-фикстура ---
if (-not $SkipFixture) {
    $fixturePath = Join-Path $backupDirFull "fixture_$timestamp.json"
    Write-Log "Выгрузка фикстуры Django -> $fixturePath" 'Cyan'

    $fixtureCommand = "cd /app/taskmanager && PYTHONIOENCODING=utf-8 python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2"
    $fixtureContent = docker compose --project-directory $projectDir exec -T web bash -lc $fixtureCommand

    [System.IO.File]::WriteAllText(
        $fixturePath,
        $fixtureContent,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Log "Фикстура сохранена." 'Green'
}

# --- экспорт образа web ---
Write-Log "Сохранение образа constructor-web..." 'Cyan'
$webImageTar = Join-Path $backupDirFull "constructor-web_$timestamp.tar"

docker compose --project-directory $projectDir build web | Out-Null
docker image save -o $webImageTar constructor-web:latest
Write-Log "Образ сохранён -> $webImageTar" 'Green'

# --- докладываем docker-compose.yml и .env для удобства ---
Copy-Item -Path (Join-Path $projectDir "docker-compose.yml") -Destination (Join-Path $backupDirFull "docker-compose.yml") -Force
if (Test-Path (Join-Path $projectDir ".env")) {
    Copy-Item -Path (Join-Path $projectDir ".env") -Destination (Join-Path $backupDirFull ".env") -Force
}

Write-Log "Снапшот готов: $backupDirFull" 'Yellow'
Get-ChildItem $backupDirFull | ForEach-Object {
    Write-Host ("  - {0}`t{1:N0} bytes" -f $_.Name, $_.Length)
}
Write-Log "Можно копировать каталог C:\constructor\backup на внешнее хранилище." 'Green'
