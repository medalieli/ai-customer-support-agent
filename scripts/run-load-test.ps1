param(
  [int]$Requests = 100,
  [int]$Warmup = 8,
  [string]$ConcurrencyLevels = "1,4,8"
)
$ErrorActionPreference = "Stop"
$levels = @($ConcurrencyLevels.Split(",") | ForEach-Object {
  $level = 0
  if (-not [int]::TryParse($_.Trim(), [ref]$level) -or $level -lt 1) {
    throw "ConcurrencyLevels must be comma-separated positive integers"
  }
  $level
})
$requestsPerLevel = [Math]::Ceiling($Requests / $levels.Count)
$runId = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$project = "novacart-load-$runId"
$env:POSTGRES_HOST_PORT = "25432"
$env:API_HOST_PORT = "28000"
$env:FRONTEND_HOST_PORT = "23000"
$env:MOCK_COMMERCE_HOST_PORT = "28080"
$env:MOCK_CRM_HOST_PORT = "28090"
$env:NEXT_PUBLIC_API_URL = "http://localhost:28000"
$env:E2E_AGENT_PROVIDER = "deterministic"
$composeArgs = @("-p", $project, "-f", "compose.yaml", "-f", "compose.e2e.yaml")
try {
  docker compose @composeArgs up --build --wait -d
  if ($LASTEXITCODE) { throw "Disposable load stack failed to start" }
  python performance/load_test.py --base-url http://localhost:28000 --requests $Warmup --concurrency 1 --approve-writes
  if ($LASTEXITCODE) { throw "Load warm-up failed" }
  foreach ($concurrency in $levels) {
    python performance/load_test.py --base-url http://localhost:28000 --requests $requestsPerLevel --concurrency $concurrency
    if ($LASTEXITCODE) { throw "Load validation failed at concurrency $concurrency" }
  }
  python performance/control_test.py --base-url http://localhost:28000 --redis-container "$project-redis-1"
  if ($LASTEXITCODE) { throw "Live control-limit validation failed" }
  $queue = docker compose @composeArgs exec -T redis redis-cli --raw LLEN arq:queue
  if ($LASTEXITCODE -or [int]$queue -ne 0) { throw "Worker queue did not recover to zero" }
  Write-Output "Queue recovery verified: $queue pending jobs"
} finally {
  docker compose @composeArgs down --volumes --remove-orphans
}
