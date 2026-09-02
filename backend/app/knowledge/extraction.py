import io
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

ALLOWED_SUFFIXES = {".md", ".txt", ".pdf"}
MAX_FILE_BYTES = 2 * 1024 * 1024


class FileValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    section: str | None
    page: int | None


def clean_text(value: str) -> str:
    value = value.replace("\x00", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def validate_file(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise FileValidationError("unsupported_file_type")
    if not content or len(content) > MAX_FILE_BYTES:
        raise FileValidationError("invalid_file_size")
    return suffix


def extract_markdown(content: bytes) -> list[ExtractedSection]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileValidationError("invalid_text_encoding") from exc
    sections: list[ExtractedSection] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            cleaned = clean_text("\n".join(buffer))
            if cleaned:
                sections.append(ExtractedSection(cleaned, heading, None))
            heading = line.lstrip("# ").strip() or None
            buffer = []
        else:
            buffer.append(line)
    cleaned = clean_text("\n".join(buffer))
    if cleaned:
        sections.append(ExtractedSection(cleaned, heading, None))
    return sections


def extract_text(content: bytes) -> list[ExtractedSection]:
    try:
        cleaned = clean_text(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FileValidationError("invalid_text_encoding") from exc
    return [ExtractedSection(cleaned, None, None)] if cleaned else []


def extract_pdf(content: bytes) -> list[ExtractedSection]:
    try:
        reader = PdfReader(io.BytesIO(content))
        sections = [
            ExtractedSection(clean_text(page.extract_text() or ""), None, number)
            for number, page in enumerate(reader.pages, start=1)
        ]
    except Exception as exc:
        raise FileValidationError("invalid_pdf") from exc
    return [section for section in sections if section.text]


def extract(filename: str, content: bytes) -> list[ExtractedSection]:
    suffix = validate_file(filename, content)
    if suffix == ".md":
        sections = extract_markdown(content)
    elif suffix == ".txt":
        sections = extract_text(content)
    else:
        sections = extract_pdf(content)
    if not sections:
        raise FileValidationError("no_extractable_text")
    return sections
