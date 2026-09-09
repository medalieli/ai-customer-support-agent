#!/usr/bin/env bash
set -euo pipefail
action="${1:-start}"
project="${NOVACART_DEMO_PROJECT:-novacart-demo}"
[[ "$project" =~ ^novacart-demo(-[a-z0-9]+)*$ ]] || { echo "Invalid demo project name" >&2; exit 2; }
compose=(docker compose -p "$project" -f compose.yaml -f compose.demo.yaml)

[[ -f .env ]] || cp .env.example .env
case "$action" in
  start|openai)
    DEMO_APP_ENV=development "${compose[@]}" up --build --wait -d
    echo "NovaCart demo is ready at http://localhost:3000 (OpenAI generation mode)."
    ;;
  seed) "${compose[@]}" exec -T api sh -c 'python -m app.seed && python -m app.knowledge.seed' ;;
  reset)
    # The validated project name scopes deletion to the selected fictional demo volumes.
    "${compose[@]}" down --volumes --remove-orphans
    DEMO_APP_ENV=development "${compose[@]}" up --build --wait -d
    ;;
  stop) "${compose[@]}" stop ;;
  clean) "${compose[@]}" down --volumes --remove-orphans ;;
  *) echo "Usage: scripts/demo.sh {start|openai|seed|reset|stop|clean}" >&2; exit 2 ;;
esac
