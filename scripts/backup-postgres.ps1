param(
  [string]$ComposeProject = "novacart-support",
  [string]$Database = "novacart",
  [string]$DatabaseUser = "novacart",
  [string]$Output = "backups/novacart.dump"
)
$ErrorActionPreference = "Stop"
if ($ComposeProject -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]+$' -or $Database -notmatch '^[a-zA-Z0-9_]+$') { throw "Unsafe project or database name" }
$container = docker compose -p $ComposeProject ps -q postgres
if (-not $container) { throw "PostgreSQL container for project '$ComposeProject' is not running" }
$target = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\$Output"))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\backups"))
if (-not $target.StartsWith("$backupRoot$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) { throw "Backup must stay inside the repository backups directory" }
New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
docker exec $container pg_dump -U $DatabaseUser -d $Database --format=custom --no-owner --no-acl --file=/tmp/novacart-backup.dump
if ($LASTEXITCODE) { throw "pg_dump failed" }
docker cp "${container}:/tmp/novacart-backup.dump" $target
docker exec $container rm /tmp/novacart-backup.dump
if ($LASTEXITCODE) { throw "Backup copy failed" }
Write-Output $target
