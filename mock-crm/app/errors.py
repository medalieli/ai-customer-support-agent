from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class CrmError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        self.status, self.code, self.message = status, code, message
        self.retryable, self.retry_after = retryable, retry_after


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(CrmError)
    async def crm_error(request: Request, exc: CrmError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
            },
            headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation",
                    "message": "The request is invalid.",
                    "retryable": False,
                }
            },
        )
