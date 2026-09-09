"""Validate repository-local Markdown links and image files without network access."""

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    errors: list[str] = []
    checked = 0
    documents = 0
    for name in sorted(set(names)):
        source = root / name
        if source.suffix != ".md" or not source.is_file():
            continue
        documents += 1
        content = re.sub(
            r"^```.*?^```[^\n]*",
            "",
            source.read_text(encoding="utf-8"),
            flags=re.MULTILINE | re.DOTALL,
        )
        for match in re.finditer(r"\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", content):
            link = urlsplit(match.group(1).strip("<>"))
            if link.scheme or link.netloc or not link.path:
                continue
            target = (source.parent / unquote(link.path)).resolve()
            checked += 1
            if not target.is_relative_to(root) or not target.exists():
                errors.append(
                    f"{name}: missing or out-of-repository target {link.path}"
                )
            elif target.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                data = target.read_bytes()
                if not (
                    data.startswith(b"\xff\xd8\xff")
                    or data.startswith(b"\x89PNG\r\n\x1a\n")
                ):
                    errors.append(f"{name}: invalid image signature {link.path}")
    for error in errors:
        print(error)
    print(
        f"Checked {checked} local links/images in {documents} Markdown files; {len(errors)} errors."
    )
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
