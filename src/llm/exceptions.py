class LLMError(Exception):
    """Base exception for all LLM client operations."""
    pass


class LLMConfigurationError(LLMError):
    """Raised when LLM configuration (URL, key, model) is missing or invalid."""
    pass


class LLMTransportError(LLMError):
    """Raised when network connection fails, times out, or returns a non-200 HTTP status."""
    pass


class LLMOutputError(LLMError):
    """Raised when the LLM response cannot be parsed into expected JSON or Pydantic model."""
    def __init__(self, message: str, raw_response: str | None = None):
        super().__init__(message)
        self.raw_response = raw_response
