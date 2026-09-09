"""Fixed safe ingestion failures; never carry source or driver exception text."""


class KnowledgeIngestionError(Exception):
    """Base application boundary error."""

    message = "Knowledge ingestion failed."

    def __init__(self) -> None:
        """Expose only a fixed message."""
        super().__init__(self.message)


class UnsupportedMediaTypeError(KnowledgeIngestionError):
    """The source format is outside the explicit baseline."""

    message = "Unsupported document media type."


class DocumentTooLargeError(KnowledgeIngestionError):
    """Source exceeds the configured byte limit."""

    message = "Document exceeds the source size limit."


class DocumentDecodeError(KnowledgeIngestionError):
    """Strict UTF-8 decoding failed."""

    message = "Document must use valid UTF-8."


class DocumentValidationError(KnowledgeIngestionError):
    """Source content or input contract is invalid."""

    message = "Invalid document input."


class ParserError(KnowledgeIngestionError):
    """Unexpected parser failure."""

    message = "Document parsing failed."


class ChunkingError(KnowledgeIngestionError):
    """Chunk generation failed or exceeded its resource bound."""

    message = "Document chunking failed."


class KnowledgePersistenceError(KnowledgeIngestionError):
    """Persistence failed; commit outcome can be unknown after disconnect."""

    message = "Knowledge persistence failed."
