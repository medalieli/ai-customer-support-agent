import hashlib
from dataclasses import dataclass

from app.knowledge.extraction import ExtractedSection


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    token_count: int
    checksum: str
    section: str | None
    page: int | None


def chunk_sections(
    sections: list[ExtractedSection], size_words: int, overlap_words: int
) -> list[TextChunk]:
    if size_words <= 0 or overlap_words < 0 or overlap_words >= size_words:
        raise ValueError("invalid_chunk_configuration")
    chunks: list[TextChunk] = []
    step = size_words - overlap_words
    for section in sections:
        words = section.text.split()
        for start in range(0, len(words), step):
            window = words[start : start + size_words]
            if not window:
                continue
            text = " ".join(window)
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    text=text,
                    token_count=len(window),
                    checksum=hashlib.sha256(text.encode()).hexdigest(),
                    section=section.section,
                    page=section.page,
                )
            )
            if start + size_words >= len(words):
                break
    return chunks
