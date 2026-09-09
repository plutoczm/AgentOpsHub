"""Small deterministic UTF-8 and fenced Markdown scanner, without rendering."""

import re

from app.knowledge.errors import (
    DocumentDecodeError,
    DocumentTooLargeError,
    DocumentValidationError,
    UnsupportedMediaTypeError,
)
from app.knowledge.models import DocumentInput, ParsedDocument, Section

HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def parse_document(source: DocumentInput, *, max_source_bytes: int) -> ParsedDocument:
    """Check bytes before decoding; normalize only BOM and line endings."""
    if len(source.content) > max_source_bytes:
        raise DocumentTooLargeError()
    if source.media_type not in ("text/plain", "text/markdown"):
        raise UnsupportedMediaTypeError()
    try:
        normalized = source.content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise DocumentDecodeError() from None
    if not normalized.strip() or any(
        (ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) <= 159 for char in normalized
    ):
        raise DocumentValidationError()
    if source.media_type == "text/plain":
        return ParsedDocument(
            text=normalized,
            media_type=source.media_type,
            sections=(Section(start=0, end=len(normalized)),),
        )
    return markdown_structure(normalized)


def markdown_structure(text: str) -> ParsedDocument:
    """Recognize ATX H1-H6 outside backtick/tilde fences, preserving every character."""
    sections: list[Section] = []
    fences: list[tuple[int, int]] = []
    ancestors: list[tuple[int, str]] = []
    section_start = offset = 0
    path: tuple[str, ...] = ()
    fence_char = ""
    fence_size = fence_start = 0
    for line in text.splitlines(keepends=True):
        raw = line.removesuffix("\n")
        match = FENCE.match(raw)
        if fence_char:
            if (
                match
                and match[1][0] == fence_char
                and len(match[1]) >= fence_size
                and not match[2].strip()
            ):
                fences.append((fence_start, offset + len(line)))
                fence_char = ""
        elif match and not (match[1][0] == "`" and "`" in match[2]):
            fence_char, fence_size, fence_start = match[1][0], len(match[1]), offset
        else:
            heading = HEADING.match(raw)
            if heading:
                if offset > section_start:
                    sections.append(Section(start=section_start, end=offset, path=path))
                level = len(heading[1])
                label = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
                while ancestors and ancestors[-1][0] >= level:
                    ancestors.pop()
                ancestors.append((level, label))
                path = tuple(item[1] for item in ancestors)
                section_start = offset
        offset += len(line)
    if fence_char:
        fences.append((fence_start, len(text)))
    if section_start < len(text):
        sections.append(Section(start=section_start, end=len(text), path=path))
    return ParsedDocument(
        text=text, media_type="text/markdown", sections=tuple(sections), fences=tuple(fences)
    )
