from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_compose_preserves_non_root_service_requirements() -> None:
    compose = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD: !reset null" in compose
    assert "cap_add: [CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]" in compose
    assert 'user: "101:101"' in compose
    assert "cap_add: [NET_BIND_SERVICE]" not in compose


def test_non_root_nginx_uses_writable_temporary_paths() -> None:
    nginx = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    directories = (
        "client_temp",
        "proxy_temp",
        "fastcgi_temp",
        "uwsgi_temp",
        "scgi_temp",
    )
    for directory in directories:
        assert f"/tmp/{directory}" in nginx


def test_load_wrapper_parses_documented_concurrency_matrix() -> None:
    wrapper = (ROOT / "scripts" / "run-load-test.ps1").read_text(encoding="utf-8")
    assert '[string]$ConcurrencyLevels = "1,4,8"' in wrapper
    assert '$ConcurrencyLevels.Split(",")' in wrapper
    assert "$requestsPerLevel = [Math]::Ceiling($Requests / $levels.Count)" in wrapper


def test_control_probe_supports_project_python_version() -> None:
    probe = (ROOT / "performance" / "control_test.py").read_text(encoding="utf-8")
    assert "timezone.utc" in probe
    assert "from datetime import UTC" not in probe
