class OnleiheError(Exception):
    """Base exception for onleihe."""


class OnleiheAPIError(OnleiheError):
    def __init__(self, message: str, *, status_code: int | None = None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class OnleiheAuthError(OnleiheAPIError):
    """Raised when authentication or token refresh fails."""


class UnsupportedEndpointError(OnleiheError):
    """Raised for known missing API coverage."""
