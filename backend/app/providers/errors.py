from app.providers.models import ProviderErrorCode


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: ProviderErrorCode,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
