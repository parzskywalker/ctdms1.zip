class BusinessError(Exception):
    """A rule / validation failure that should be shown to the user (HTTP 400 on the API)."""

    def __init__(self, message: str, *, field: str | None = None):
        self.message = message
        self.field = field
        super().__init__(message)
