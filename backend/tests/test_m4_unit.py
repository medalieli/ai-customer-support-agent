from io import BytesIO
from typing import Any, cast

import pytest
from pypdf import PdfWriter

from app.knowledge.chunking import chunk_sections
from app.knowledge.embeddings import DeterministicFakeEmbeddings
from app.knowledge.extraction import (
    ExtractedSection,
    FileValidationError,
    clean_text,
    extract,
    validate_file,
)
from app.knowledge.retrieval import local_rerank, normalized_tokens


def test_file_validation_and_text_extraction() -> None:
    assert validate_file("policy.md", b"# Policy\nText") == ".md"
    assert (
        extract("policy.txt", b"Multiple   spaces\n\n\nText")[0].text == "Multiple spaces\n\nText"
    )
    sections = extract("policy.md", b"# Shipping\nFirst rule.\n## Tracking\nSecond rule.")
    assert [(item.section, item.text) for item in sections] == [
        ("Shipping", "First rule."),
        ("Tracking", "Second rule."),
    ]
    assert clean_text("a\x00  b") == "a b"
    with pytest.raises(FileValidationError, match="unsupported_file_type"):
        validate_file("image.png", b"data")
    with pytest.raises(FileValidationError, match="invalid_file_size"):
        validate_file("empty.txt", b"")
    with pytest.raises(FileValidationError, match="invalid_text_encoding"):
        extract("bad.txt", b"\xff")


def test_pdf_is_recognized_and_empty_pdf_fails_safely() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(FileValidationError, match="no_extractable_text"):
        extract("empty.pdf", output.getvalue())
    with pytest.raises(FileValidationError, match="invalid_pdf"):
        extract("bad.pdf", b"not a pdf")


def test_chunking_is_deterministic_and_overlapping() -> None:
    sections = [ExtractedSection("one two three four five six", "Rules", 2)]
    first = chunk_sections(sections, 4, 2)
    second = chunk_sections(sections, 4, 2)
    assert first == second
    assert [item.text for item in first] == ["one two three four", "three four five six"]
    assert first[0].section == "Rules" and first[0].page == 2
    with pytest.raises(ValueError, match="invalid_chunk_configuration"):
        chunk_sections(sections, 4, 4)


@pytest.mark.asyncio
async def test_fake_embeddings_are_deterministic_and_failure_is_controllable() -> None:
    provider = DeterministicFakeEmbeddings(1536)
    first = await provider.embed(["shipping tracking"])
    assert first == await provider.embed(["shipping tracking"])
    assert len(first[0]) == 1536
    with pytest.raises(RuntimeError, match="synthetic_embedding_failure"):
        await provider.embed(["[[EMBEDDING_FAILURE]]"])


def test_multilingual_local_reranking() -> None:
    assert "shipping" in normalized_tokens("livraison")
    assert local_rerank("garantie", "twelve month warranty and garantie") > 0
    assert local_rerank("", "anything") == 0


@pytest.mark.asyncio
async def test_arq_job_queue_uses_validated_redis_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.infrastructure import redis as redis_module

    sentinel = object()
    captured: list[Any] = []

    async def fake_create_pool(settings: Any) -> Any:
        captured.append(settings)
        return sentinel

    monkeypatch.setattr(redis_module, "create_pool", fake_create_pool)
    result = await redis_module.create_job_queue(Settings(redis_connect_timeout_seconds=0.25))

    assert cast(Any, result) is sentinel
    assert captured[0].conn_timeout == 1
