param([ValidateSet("all", "desktop", "mobile", "backend", "openai")][string]$Suite = "all")
$ErrorActionPreference = "Stop"
$suites = if ($Suite -eq "all") { @("desktop", "mobile", "backend") } else { @($Suite) }
$env:POSTGRES_HOST_PORT = "15432"
$env:API_HOST_PORT = "18000"
$env:FRONTEND_HOST_PORT = "13000"
$env:MOCK_COMMERCE_HOST_PORT = "18080"
$env:MOCK_CRM_HOST_PORT = "18090"
$env:NEXT_PUBLIC_API_URL = "http://localhost:18000"
$env:PLAYWRIGHT_BASE_URL = "http://localhost:13000"
$env:NOVACART_POSTGRES_HOST = "localhost"
$env:NOVACART_POSTGRES_PORT = "15432"
$env:NOVACART_POSTGRES_DB = "novacart"
$env:NOVACART_POSTGRES_USER = "novacart"
$env:NOVACART_POSTGRES_PASSWORD = "isolated-e2e-password"
$env:NOVACART_MOCK_COMMERCE_URL = "http://localhost:18080"
$env:NOVACART_MOCK_CRM_URL = "http://localhost:18090"
foreach ($selected in $suites) {
    $runId = [Guid]::NewGuid().ToString("N").Substring(0, 10)
    $project = "novacart-e2e-$runId"
    $env:E2E_RUN_ID = $runId
    $env:E2E_PROJECT = $project
    $env:E2E_AGENT_PROVIDER = if ($selected -eq "openai") { "openai" } else { "deterministic" }
    $composeArgs = @("-p", $project, "-f", "compose.yaml", "-f", "compose.e2e.yaml")
    try {
        docker compose @composeArgs up --build --wait -d
        if ($LASTEXITCODE -ne 0) { throw "Isolated E2E stack failed to start" }
        if ($selected -eq "backend") {
            $env:NOVACART_RUN_DB_TESTS = "1"
            Push-Location backend
            try {
                & ..\.venv\Scripts\python.exe -m pytest
                if ($LASTEXITCODE -ne 0) { throw "Backend suite failed" }
            } finally { Pop-Location; Remove-Item Env:NOVACART_RUN_DB_TESTS }
        } else {
            Push-Location frontend
            try {
                if ($selected -eq "openai") {
                    npx playwright test live-demo.spec.ts --project=desktop
                } else {
                    npx playwright test acceptance.spec.ts workspaces.spec.ts --project=$selected
                }
                if ($LASTEXITCODE -ne 0) { throw "Playwright failed" }
            } finally { Pop-Location }
        }
        $before = docker compose @composeArgs exec -T postgres psql -U novacart -d novacart -Atc "SELECT count(*) FROM conversations"
        if ($LASTEXITCODE -ne 0) { throw "Persistence baseline failed" }
        docker compose @composeArgs restart postgres api
        if ($LASTEXITCODE -ne 0) { throw "Restart failed" }
        docker compose @composeArgs up --wait -d
        if ($LASTEXITCODE -ne 0) { throw "Restart readiness failed" }
        $after = docker compose @composeArgs exec -T postgres psql -U novacart -d novacart -Atc "SELECT count(*) FROM conversations"
        if ($LASTEXITCODE -ne 0 -or $before -ne $after) { throw "Persistence check failed" }
        Write-Output "Persistence verified for $selected run ${runId}: $after conversations"
    } finally {
        # Only this invocation's uniquely named disposable project is removed.
        docker compose @composeArgs down --volumes --remove-orphans
        if ($LASTEXITCODE -ne 0) { Write-Warning "Cleanup failed for $project; inspect this exact project." }
    }
}
