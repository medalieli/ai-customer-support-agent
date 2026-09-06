"""Exercise live rate, concurrency, and budget controls on the disposable stack."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--redis-container", required=True)
    args = parser.parse_args()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"Content-Type": "application/json", "X-CSRF-Token": "novacart-browser-v1"}

    def call(path: str, body: object) -> tuple[int, dict[str, str]]:
        request = Request(
            args.base_url + path,
            data=json.dumps(body).encode(),
            headers={
                **headers,
                "Idempotency-Key": f"controls-{datetime.now(timezone.utc).timestamp()}",
            },
            method="POST",
        )
        try:
            response = opener.open(request, timeout=30)
            response.read()
            return response.status, {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as exc:
            exc.read()
            return exc.code, {key.lower(): value for key, value in exc.headers.items()}

    def redis(*command: str) -> None:
        subprocess.run(
            ["docker", "exec", args.redis_container, "redis-cli", *command],
            check=True,
            capture_output=True,
            text=True,
        )

    login = Request(
        args.base_url + "/api/v1/auth/demo-login",
        data=json.dumps({"organization_slug": "novacart", "persona_key": "amira-en"}).encode(),
        headers=headers,
        method="POST",
    )
    identity = json.loads(opener.open(login, timeout=30).read())
    create = Request(
        args.base_url + "/api/v1/conversations",
        data=json.dumps({"locale": "en", "title": "controls"}).encode(),
        headers={**headers, "Idempotency-Key": "controls-conversation"},
        method="POST",
    )
    conversation = json.loads(opener.open(create, timeout=30).read())["id"]
    tenant, actor = identity["organization_id"], identity["subject_id"]
    minute = int(datetime.now(timezone.utc).timestamp()) // 60
    day = datetime.now(timezone.utc).date().isoformat()
    rate = f"control:rate:actor:{tenant}:{actor}:{minute}"
    concurrent = f"control:concurrent:actor:{tenant}:{actor}"
    tokens = f"control:tokens:{tenant}:{day}"
    endpoint = f"/api/v1/agent/threads/{conversation}/messages"

    scenarios = (
        (rate, "1000", 60, "rate"),
        (concurrent, "20", 5, "concurrency"),
        (tokens, "100000000", 3600, "token_budget"),
    )
    results: dict[str, object] = {}
    for key, value, retry_after, name in scenarios:
        redis("SET", key, value, "EX", "120")
        status, response_headers = call(endpoint, {"content": "What is the return policy?"})
        observed_retry = int(response_headers.get("retry-after", "0"))
        if status != 429 or observed_retry != retry_after:
            raise RuntimeError(f"{name} did not fail safely: {status}/{observed_retry}")
        results[name] = {"status": status, "retry_after": observed_retry}
        redis("DEL", key)

    for key, *_ in scenarios:
        redis("DEL", key)
    recovered, _ = call(endpoint, {"content": "What is the return policy?"})
    if recovered not in {200, 201, 202}:
        raise RuntimeError(f"controls did not recover: {recovered}")
    results["recovered_status"] = recovered
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
