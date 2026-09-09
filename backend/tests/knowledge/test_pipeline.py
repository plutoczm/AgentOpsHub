import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.knowledge.chunking import chunk_document, content_hash
from app.knowledge.errors import (
    ChunkingError,
    DocumentDecodeError,
    DocumentTooLargeError,
    DocumentValidationError,
    UnsupportedMediaTypeError,
)
from app.knowledge.models import (
    ChunkingConfig,
    DocumentInput,
    IngestionConfig,
    KnowledgeIngestionContext,
)
from app.knowledge.parsing import parse_document

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "examples/knowledge"


def source(content: bytes = b"hello", media_type: str = "text/plain") -> DocumentInput:
    return DocumentInput(
        source_key="fixture.txt", title="Fixture", media_type=media_type, content=content
    )


def test_context_is_strict_frozen_and_trusted() -> None:
    tenant = uuid4()
    context = KnowledgeIngestionContext(
        tenant_id=tenant, namespace="supportops", request_id=uuid4()
    )
    assert context.tenant_id == tenant and context.namespace == "supportops"
    with pytest.raises(ValidationError):
        context.namespace = "datacopilot"  # type: ignore[misc]


@pytest.mark.parametrize("namespace", ["", "SupportOps", "../x", "a/b", "a" * 65, "x\n"])
def test_invalid_namespace(namespace: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeIngestionContext(tenant_id=uuid4(), namespace=namespace)


@pytest.mark.parametrize(
    "field", ["tenant_id", "namespace", "id", "created_at", "updated_at", "revision_number"]
)
def test_document_rejects_persistence_and_ownership_injection(field: str) -> None:
    payload = source().model_dump()
    payload[field] = "injected"
    with pytest.raises(ValidationError):
        DocumentInput.model_validate(payload)


@pytest.mark.parametrize(
    "key", ["", "../secret", "a/b", "a\\b", "https://example.com", "a..b", "x\n", "a" * 201]
)
def test_invalid_source_identity(key: str) -> None:
    with pytest.raises(ValidationError):
        DocumentInput(source_key=key, title="Title", media_type="text/plain", content=b"x")


@pytest.mark.parametrize("title", [" ", "bad\x00title", "a" * 301])
def test_invalid_title(title: str) -> None:
    with pytest.raises(ValidationError):
        DocumentInput(source_key="x", title=title, media_type="text/plain", content=b"x")


@pytest.mark.parametrize("media", ["text/plain", "text/markdown"])
def test_utf8_bom_newlines_and_meaningful_whitespace(media: str) -> None:
    text = "\ufeff# Heading\r\n\r\n  SELECT x;  \r\tindent\r\n"
    parsed = parse_document(source(text.encode("utf-8"), media), max_source_bytes=1000)
    assert parsed.text == "# Heading\n\n  SELECT x;  \n\tindent\n"
    assert parsed.media_type == media
    unicode_text = "\u4e2d\u6587 \U0001f600"
    assert parse_document(source(unicode_text.encode()), max_source_bytes=1000).text == unicode_text


@pytest.mark.parametrize("content", [b"\xff", b"\xc3", b"\xff\xfea\x00"])
def test_decode_rejects_invalid_utf8(content: bytes) -> None:
    with pytest.raises(DocumentDecodeError):
        parse_document(source(content), max_source_bytes=1000)


@pytest.mark.parametrize("content", [b"", b" \n\t", b"a\x00b", b"a\x01b", b"a\x7fb", b"a\xc2\x85b"])
def test_binary_and_empty_rejection(content: bytes) -> None:
    with pytest.raises(DocumentValidationError):
        parse_document(source(content), max_source_bytes=1000)


def test_size_check_precedes_decode_and_media_parsing() -> None:
    with pytest.raises(DocumentTooLargeError):
        parse_document(source(b"\xff" * 11, "application/pdf"), max_source_bytes=10)
    assert parse_document(source(b"a" * 10), max_source_bytes=10).text == "a" * 10
    with pytest.raises(UnsupportedMediaTypeError):
        parse_document(source(b"x", "application/pdf"), max_source_bytes=10)


@pytest.mark.parametrize(
    "values",
    [
        {"max_chars": 63},
        {"max_chars": 16385},
        {"max_chars": True},
        {"overlap_chars": -1},
        {"max_chars": 64, "overlap_chars": 64},
        {"max_chars": 64, "overlap_chars": 33},
    ],
)
def test_chunk_config_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChunkingConfig.model_validate(values)


def test_config_fingerprint_and_source_bounds() -> None:
    config = ChunkingConfig()
    assert config.fingerprint == hashlib.sha256(b"chars-v1:max=1000;overlap=100").hexdigest()
    assert config.fingerprint != ChunkingConfig(overlap_chars=0).fingerprint
    assert IngestionConfig().max_source_bytes == 1048576
    for size in [0, 4194305]:
        with pytest.raises(ValidationError):
            IngestionConfig(max_source_bytes=size)


def test_plain_text_golden_windows_overlap_and_hash() -> None:
    parsed = parse_document(source(b"a" * 130), max_source_bytes=200)
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=8))
    assert [(c.character_start, c.character_end) for c in chunks] == [
        (0, 64),
        (56, 120),
        (112, 130),
    ]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert [len(c.content) for c in chunks] == [64, 64, 18]
    assert (
        content_hash("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert chunks == chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=8))


def test_plain_paragraph_boundary_preference() -> None:
    text = "a" * 35 + "\n\n" + "b" * 60
    chunks = chunk_document(
        parse_document(source(text.encode()), max_source_bytes=200),
        ChunkingConfig(max_chars=64, overlap_chars=0),
    )
    assert [(c.character_start, c.character_end) for c in chunks] == [(0, 37), (37, 97)]


def test_markdown_heading_ancestry_golden() -> None:
    text = "# A\nintro\n## B\nbody\n### C\nend\n## D\nlast\n"
    parsed = parse_document(source(text.encode(), "text/markdown"), max_source_bytes=200)
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=0))
    assert [(c.character_start, c.character_end, c.section_path) for c in chunks] == [
        (0, 10, ("A",)),
        (10, 20, ("A", "B")),
        (20, 30, ("A", "B", "C")),
        (30, 40, ("A", "D")),
    ]
    assert "".join(c.content for c in chunks) == text


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_code_fences_are_inert_and_heading_like_lines_stay_in_section(fence: str) -> None:
    text = f"# SQL\n\n{fence}sql\n# not a heading\n  SELECT  1;\n{fence}\n"
    parsed = parse_document(source(text.encode(), "text/markdown"), max_source_bytes=200)
    assert len(parsed.sections) == 1
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=0))
    assert len(chunks) == 1 and chunks[0].content == text


def test_fitting_fence_moves_whole_and_oversize_fence_preserves_source() -> None:
    text = "# X\n" + "intro " * 6 + "\n```sql\nSELECT  1;\n```\n" + "tail " * 10
    parsed = parse_document(source(text.encode(), "text/markdown"), max_source_bytes=1000)
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=8))
    fence = "```sql\nSELECT  1;\n```\n"
    assert any(fence in c.content for c in chunks)
    large = "# X\n```sql\n" + "x" * 200 + "\n```\n"
    parsed = parse_document(source(large.encode(), "text/markdown"), max_source_bytes=1000)
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=0))
    assert all(len(c.content) <= 64 for c in chunks)
    assert "".join(c.content for c in chunks) == large


def test_unclosed_fences_preamble_and_skipped_heading_levels() -> None:
    text = "intro\n# A ###\n### C\n~~~\n## inert\n"
    parsed = parse_document(source(text.encode(), "text/markdown"), max_source_bytes=200)
    assert [s.path for s in parsed.sections] == [(), ("A",), ("A", "C")]
    assert parsed.fences[-1][1] == len(text)


def test_chunk_count_resource_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.knowledge.chunking.MAX_CHUNKS", 1)
    parsed = parse_document(source(b"x" * 130), max_source_bytes=200)
    with pytest.raises(ChunkingError):
        chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=0))


@pytest.mark.parametrize("path", sorted(CORPUS.glob("*/*.md")), ids=lambda p: p.stem)
def test_corpus_reproduction_and_provenance(path: Path) -> None:
    document = DocumentInput(
        source_key=path.name, title=path.stem, media_type="text/markdown", content=path.read_bytes()
    )
    parsed = parse_document(document, max_source_bytes=1048576)
    expected = chunk_document(parsed, ChunkingConfig())
    assert len(expected) == 3
    assert [c.chunk_index for c in expected] == [0, 1, 2]
    for _ in range(3):
        again = parse_document(document, max_source_bytes=1048576)
        assert again == parsed and chunk_document(again, ChunkingConfig()) == expected
    for chunk in expected:
        assert chunk.content == parsed.text[chunk.character_start : chunk.character_end]
        assert chunk.content_sha256 == content_hash(chunk.content)
        assert chunk.section_path
    assert "".join(c.content for c in expected) == parsed.text


@pytest.mark.parametrize("prefix_size", [0, 33, 54, 63])
@pytest.mark.parametrize("code_size", [1, 20, 54])
def test_fence_boundary_matrix_has_full_source_coverage(prefix_size: int, code_size: int) -> None:
    text = "# X\n" + "x" * prefix_size + "\n~~~\n" + "y" * code_size + "\n~~~\n" + "tail " * 25
    parsed = parse_document(source(text.encode(), "text/markdown"), max_source_bytes=1000)
    chunks = chunk_document(parsed, ChunkingConfig(max_chars=64, overlap_chars=8))
    literal_fence = "~~~\n" + "y" * code_size + "\n~~~\n"
    assert any(literal_fence in c.content for c in chunks)
    covered: set[int] = set()
    for chunk in chunks:
        assert len(chunk.content) <= 64
        assert chunk.content == text[chunk.character_start : chunk.character_end]
        covered.update(range(chunk.character_start, chunk.character_end))
    assert covered == set(range(len(text)))
