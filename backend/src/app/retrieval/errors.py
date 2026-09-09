"""Fixed retrieval failures without query, content or database payloads."""


class KnowledgeRetrievalError(Exception):
    """Safe application boundary failure."""

    message = "Knowledge retrieval failed."

    def __init__(self) -> None:
        """Carry only the fixed category message."""
        super().__init__(self.message)


class InvalidSearchRequestError(KnowledgeRetrievalError):
    """Untrusted input or trusted-context contract validation failed."""

    message = "Invalid knowledge search request or context."


class RetrievalBackendError(KnowledgeRetrievalError):
    """Backend I/O failed; raw PostgreSQL exception text is suppressed."""

    message = "Knowledge retrieval backend unavailable."


class RetrievalInvariantError(KnowledgeRetrievalError):
    """Returned data violates the retrieval contract."""

    message = "Invalid knowledge retrieval result."
