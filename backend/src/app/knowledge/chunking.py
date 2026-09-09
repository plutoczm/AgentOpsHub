"""Deterministic section/paragraph windows with exact source-range provenance."""

import hashlib

from app.knowledge.errors import ChunkingError
from app.knowledge.models import ChunkDraft, ChunkingConfig, ParsedDocument

CHUNKER_VERSION = "boundary-chars-v1"
MAX_CHUNKS = 8192


def content_hash(text: str) -> str:
    """Hash exact normalized UTF-8 text, including meaningful and terminal whitespace."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(document: ParsedDocument, config: ChunkingConfig) -> tuple[ChunkDraft, ...]:
    """Prefer paragraph, line, then whitespace boundaries; hard-slice only as fallback.

    Overlap is exact inside a section unless reduced to preserve a fitting fence.
    Sections never overlap. Oversize fences retain literal bytes across hard slices.
    """
    result: list[ChunkDraft] = []
    text = document.text
    for section in document.sections:
        start = section.start
        previous_end = start
        while start < section.end:
            limit = min(start + config.max_chars, section.end)
            end = limit
            if limit < section.end:
                floor = max(previous_end + 1, start + config.overlap_chars + 1)
                for boundary in ("\n\n", "\n", " ", "\t"):
                    pos = text.rfind(boundary, floor, limit)
                    if pos >= floor:
                        end = pos + len(boundary)
                        break
                for fence_start, fence_end in document.fences:
                    if (
                        fence_end - fence_start <= config.max_chars
                        and fence_start < end < fence_end
                    ):
                        if fence_start > previous_end:
                            end = fence_start
                        elif fence_end <= limit:
                            end = fence_end
                        break
            if end <= previous_end:
                raise ChunkingError()
            body = text[start:end]
            result.append(
                ChunkDraft(
                    chunk_index=len(result),
                    content=body,
                    content_sha256=content_hash(body),
                    section_path=section.path,
                    character_start=start,
                    character_end=end,
                )
            )
            if len(result) > MAX_CHUNKS:
                raise ChunkingError()
            if end == section.end:
                break
            next_start = max(start + 1, end - config.overlap_chars)
            for fence_start, fence_end in document.fences:
                if fence_end - fence_start <= config.max_chars:
                    if fence_start < next_start < fence_end:
                        next_start = fence_end
                    elif end == fence_start:
                        next_start = end
            previous_end, start = end, next_start
    if not result:
        raise ChunkingError()
    return tuple(result)
