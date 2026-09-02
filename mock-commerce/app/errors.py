from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class CommerceError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


def payload(code: str, message: str, retryable: bool = False) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(CommerceError)
    async def commerce_error(request: Request, exc: CommerceError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return JSONResponse(
            status_code=exc.status,
            content=payload(exc.code, exc.message, exc.retryable),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=payload("validation", "The request is invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=payload(
                "unavailable", "The commerce service is temporarily unavailable.", True
            ),
        )
