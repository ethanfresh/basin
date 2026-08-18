"""On-disk store of filing documents.

Verification, extraction and citation all need the same documents, and a 10-K
runs to several megabytes, so they are fetched once and kept. Raw HTML is what
gets stored — not the flattened text — because the parse will change and
re-deriving text from a local file is free while re-fetching is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from basin.documents.text import Document, parse

CORPUS = Path("data/corpus")


@dataclass(frozen=True)
class StoredDocument:
    accession: str
    name: str
    path: Path

    @property
    def size(self) -> int:
        return self.path.stat().st_size


def document_path(accession: str, name: str, *, root: Path = CORPUS) -> Path:
    return root / accession / name


def store(accession: str, name: str, raw: str, *, root: Path = CORPUS) -> StoredDocument:
    path = document_path(accession, name, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8", errors="replace")
    return StoredDocument(accession, name, path)


def is_stored(accession: str, name: str, *, root: Path = CORPUS) -> bool:
    return document_path(accession, name, root=root).exists()


def load_raw(accession: str, name: str, *, root: Path = CORPUS) -> str | None:
    path = document_path(accession, name, root=root)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else None


def load(accession: str, name: str, *, root: Path = CORPUS) -> Document | None:
    """Parsed document, with page and line coordinates."""
    raw = load_raw(accession, name, root=root)
    return parse(raw) if raw is not None else None


def fetch(client, cik: str, accession: str, name: str, *, root: Path = CORPUS) -> str:
    """Raw HTML for one document, from the corpus or from EDGAR.

    Fetching is the slow, rate-limited part, so it happens once; every later
    parse reads the stored copy. That is what makes improving the parser cheap
    -- re-deriving text from disk costs nothing, re-downloading a 3MB 10-K for
    every filer does not.
    """
    from basin.documents.locate import document_url

    raw = load_raw(accession, name, root=root)
    if raw is None:
        raw = client.get_text(document_url(cik, accession, name))
        store(accession, name, raw, root=root)
    return raw


def stored_documents(accession: str, *, root: Path = CORPUS) -> list[StoredDocument]:
    directory = root / accession
    if not directory.exists():
        return []
    return [
        StoredDocument(accession, p.name, p)
        for p in sorted(directory.iterdir())
        if p.is_file()
    ]
