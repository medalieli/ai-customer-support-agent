param([int]$Requests = 30, [int]$Concurrency = 4)
$ErrorActionPreference = "Stop"
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
  python performance/load_test.py --base-url http://localhost:28000 --requests $Requests --concurrency $Concurrency
  if ($LASTEXITCODE) { throw "Load validation failed" }
  $queue = docker compose @composeArgs exec -T redis redis-cli --raw LLEN arq:queue
  if ($LASTEXITCODE -or [int]$queue -ne 0) { throw "Worker queue did not recover to zero" }
  Write-Output "Queue recovery verified: $queue pending jobs"
} finally {
  docker compose @composeArgs down --volumes --remove-orphans
}
