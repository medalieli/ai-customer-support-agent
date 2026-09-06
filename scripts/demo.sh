#!/usr/bin/env bash
set -euo pipefail
action="${1:-start}"
project="novacart-demo"
compose=(docker compose -p "$project" -f compose.yaml -f compose.demo.yaml)

[[ -f .env ]] || cp .env.example .env
case "$action" in
  start|deterministic)
    DEMO_APP_ENV=test DEMO_AGENT_PROVIDER=deterministic "${compose[@]}" up --build --wait -d
    echo "NovaCart demo is ready at http://localhost:3000 (deterministic mode)."
    ;;
  openai)
    : "${NOVACART_OPENAI_API_KEY:?Set NOVACART_OPENAI_API_KEY before starting OpenAI mode.}"
    DEMO_APP_ENV=development DEMO_AGENT_PROVIDER=openai "${compose[@]}" up --build --wait -d
    echo "NovaCart demo is ready at http://localhost:3000 (OpenAI generation mode)."
    ;;
  seed) "${compose[@]}" exec -T api sh -c 'python -m app.seed && python -m app.knowledge.seed' ;;
  reset)
    # The fixed project name scopes deletion to NovaCart's fictional demo volumes.
    "${compose[@]}" down --volumes --remove-orphans
    DEMO_APP_ENV=test DEMO_AGENT_PROVIDER=deterministic "${compose[@]}" up --build --wait -d
    ;;
  stop) "${compose[@]}" stop ;;
  clean) "${compose[@]}" down --volumes --remove-orphans ;;
  *) echo "Usage: scripts/demo.sh {start|deterministic|openai|seed|reset|stop|clean}" >&2; exit 2 ;;
esac
