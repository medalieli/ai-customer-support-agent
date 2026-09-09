$ErrorActionPreference = "Stop"
$runId = [Guid]::NewGuid().ToString("N").Substring(0, 10)
$project = "novacart-e2e-$runId"
$env:POSTGRES_HOST_PORT = "15432"
$env:API_HOST_PORT = "18000"
$env:FRONTEND_HOST_PORT = "13000"
$env:MOCK_COMMERCE_HOST_PORT = "18080"
$env:MOCK_CRM_HOST_PORT = "18090"
$env:NEXT_PUBLIC_API_URL = "http://localhost:18000"
$env:PLAYWRIGHT_BASE_URL = "http://localhost:13000"
$env:E2E_RUN_ID = $runId
$env:E2E_PROJECT = $project
$env:E2E_AGENT_PROVIDER = "openai"
$compose = @("-p", $project, "-f", "compose.yaml", "-f", "compose.e2e.yaml")
try {
    docker compose @compose up --build --wait -d
    if ($LASTEXITCODE -ne 0) { throw "Portfolio stack failed to start" }
    Push-Location frontend
    try {
        npx playwright test portfolio.spec.ts --project=desktop
        if ($LASTEXITCODE -ne 0) { throw "Portfolio capture failed" }
    } finally { Pop-Location }
} finally {
    # Only this invocation's unique disposable project and volumes are removed.
    docker compose @compose down --volumes --remove-orphans
}
