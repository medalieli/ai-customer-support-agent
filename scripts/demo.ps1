param([ValidateSet("start", "openai", "seed", "reset", "stop", "clean")][string]$Action = "start")
$ErrorActionPreference = "Stop"
$project = if ($env:NOVACART_DEMO_PROJECT) { $env:NOVACART_DEMO_PROJECT } else { "novacart-demo" }
if ($project -notmatch '^novacart-demo(?:-[a-z0-9]+)*$') { throw "Invalid demo project name" }
$compose = @("-p", $project, "-f", "compose.yaml", "-f", "compose.demo.yaml")

if (-not (Test-Path -LiteralPath ".env")) { Copy-Item -LiteralPath ".env.example" -Destination ".env" }

if ($Action -in @("start", "openai")) {
    $env:DEMO_APP_ENV = "development"
    docker compose @compose up --build --wait -d
    if ($LASTEXITCODE -ne 0) { throw "NovaCart demo failed to start" }
    Write-Output "NovaCart demo is ready at http://localhost:3000 (OpenAI mode)."
} elseif ($Action -eq "seed") {
    docker compose @compose exec -T api sh -c "python -m app.seed && python -m app.knowledge.seed"
} elseif ($Action -eq "reset") {
    # The validated project name scopes deletion to the selected fictional demo volumes.
    docker compose @compose down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Demo reset cleanup failed" }
    $env:DEMO_APP_ENV = "development"
    docker compose @compose up --build --wait -d
} elseif ($Action -eq "stop") {
    docker compose @compose stop
} elseif ($Action -eq "clean") {
    docker compose @compose down --volumes --remove-orphans
}
if ($LASTEXITCODE -ne 0) { throw "Demo action '$Action' failed" }
