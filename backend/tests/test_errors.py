from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


async def test_unhandled_error_response_hides_exception_details() -> None:
    app = create_app(Settings(app_env="test"))
    secret = "exception-secret-canary"

    @app.get("/_test/error")
    async def fail() -> None:
        raise RuntimeError(secret)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/_test/error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert secret not in response.text
    assert "traceback" not in response.text.lower()
