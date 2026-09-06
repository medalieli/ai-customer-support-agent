param(
  [string]$ComposeProject = "novacart-support",
  [string]$DatabaseUser = "novacart",
  [string]$Backup = "backups/novacart.dump"
)
$ErrorActionPreference = "Stop"
if ($ComposeProject -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]+$') { throw "Unsafe project name" }
$container = docker compose -p $ComposeProject ps -q postgres
if (-not $container) { throw "PostgreSQL container is not running" }
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\$Backup"))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\backups"))
if (-not $source.StartsWith("$backupRoot$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) { throw "Restore source must stay inside the repository backups directory" }
if (-not (Test-Path -LiteralPath $source)) { throw "Backup does not exist: $source" }
$drill = "novacart_restore_$([Guid]::NewGuid().ToString('N').Substring(0,10))"
try {
  docker exec $container createdb -U $DatabaseUser $drill
  if ($LASTEXITCODE) { throw "Could not create disposable restore database" }
  docker cp $source "${container}:/tmp/restore-drill.dump"
  docker exec $container pg_restore -U $DatabaseUser -d $drill --no-owner --no-acl --exit-on-error /tmp/restore-drill.dump
  if ($LASTEXITCODE) { throw "Restore failed" }
  $counts = docker exec $container psql -U $DatabaseUser -d $drill -At -c "SELECT json_build_object('organizations',count(DISTINCT o.id),'customers',count(DISTINCT c.id),'conversations',count(DISTINCT v.id),'orphan_conversations',count(*) FILTER (WHERE v.id IS NOT NULL AND c.id IS NULL)) FROM organizations o LEFT JOIN customers c ON c.organization_id=o.id LEFT JOIN conversations v ON v.organization_id=o.id AND v.customer_id=c.id;"
  if ($LASTEXITCODE -or -not $counts) { throw "Relationship verification failed" }
  $parsed = $counts | ConvertFrom-Json
  if ([int]$parsed.organizations -lt 1 -or [int]$parsed.orphan_conversations -ne 0) { throw "Restored tenant relationships are invalid" }
  Write-Output $counts
} finally {
  docker exec $container rm -f /tmp/restore-drill.dump 2>$null | Out-Null
  docker exec $container dropdb -U $DatabaseUser --if-exists $drill 2>$null | Out-Null
}
