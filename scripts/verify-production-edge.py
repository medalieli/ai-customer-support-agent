"""Repeatable black-box checks for a running production reverse proxy."""

from __future__ import annotations

import argparse
import json
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", default="http://localhost:8088")
    parser.add_argument("--https", default="https://localhost:8443")
    args = parser.parse_args()
    tls = ssl._create_unverified_context()  # noqa: S323 - local self-signed verification only
    direct = build_opener(NoRedirect())
    try:
        direct.open(args.http + "/health/live")
        raise AssertionError("HTTP did not redirect")
    except HTTPError as exc:
        assert exc.code in {301, 302, 307, 308}
        assert exc.headers["Location"].startswith(args.https)

    opener = build_opener(HTTPSHandler(context=tls))
    for path in ("/", "/chat", "/staff"):
        assert opener.open(args.https + path).status == 200
    for path in ("/docs", "/openapi.json"):
        try:
            opener.open(args.https + path)
            raise AssertionError(f"{path} was enabled")
        except HTTPError as exc:
            assert exc.code == 404

    response = opener.open(args.https + "/health/live")
    required = {
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Frame-Options",
        "X-Content-Type-Options",
    }
    assert required.issubset(response.headers)
    request = Request(
        args.https + "/api/v1/auth/staff-login",
        data=json.dumps(
            {"organization_slug": "x", "email": "x@x.test", "password": "password1"}
        ).encode(),
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        method="POST",
    )
    try:
        opener.open(request)
        raise AssertionError("untrusted origin was accepted")
    except HTTPError as exc:
        assert exc.code in {400, 403}
    oversized = Request(
        args.https + "/api/v1/auth/staff-login",
        data=b"x" * (2 * 1024 * 1024 + 1),
        headers={"Content-Type": "application/octet-stream"},
        method="POST",
    )
    try:
        opener.open(oversized)
        raise AssertionError("oversized request was accepted")
    except HTTPError as exc:
        assert exc.code == 413
    print("production edge verification passed")


if __name__ == "__main__":
    main()
