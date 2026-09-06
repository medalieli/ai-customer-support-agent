param([ValidateSet("start", "deterministic", "openai", "seed", "reset", "stop", "clean")][string]$Action = "start")
$ErrorActionPreference = "Stop"
$project = "novacart-demo"
$compose = @("-p", $project, "-f", "compose.yaml", "-f", "compose.demo.yaml")

if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env" }

if ($Action -eq "openai" -and [string]::IsNullOrWhiteSpace($env:NOVACART_OPENAI_API_KEY)) {
    throw "Set NOVACART_OPENAI_API_KEY in this shell before starting OpenAI mode."
}
if ($Action -in @("start", "deterministic", "openai")) {
    $env:DEMO_AGENT_PROVIDER = if ($Action -eq "openai") { "openai" } else { "deterministic" }
    $env:DEMO_APP_ENV = if ($Action -eq "openai") { "development" } else { "test" }
    docker compose @compose up --build --wait -d
    if ($LASTEXITCODE -ne 0) { throw "NovaCart demo failed to start" }
    Write-Output "NovaCart demo is ready at http://localhost:3000 ($env:DEMO_AGENT_PROVIDER mode)."
} elseif ($Action -eq "seed") {
    docker compose @compose exec -T api sh -c "python -m app.seed && python -m app.knowledge.seed"
} elseif ($Action -eq "reset") {
    # The fixed project name scopes deletion to NovaCart's fictional demo volumes.
    docker compose @compose down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Demo reset cleanup failed" }
    $env:DEMO_AGENT_PROVIDER = "deterministic"
    $env:DEMO_APP_ENV = "test"
    docker compose @compose up --build --wait -d
} elseif ($Action -eq "stop") {
    docker compose @compose stop
} elseif ($Action -eq "clean") {
    docker compose @compose down --volumes --remove-orphans
}
if ($LASTEXITCODE -ne 0) { throw "Demo action '$Action' failed" }
