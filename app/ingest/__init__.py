import pymupdf
from sqlmodel import delete

from app.chunking import chunks
from app.db import Chunk


def page_texts(path):
    with pymupdf.open(path) as document:
        return [page.get_text("text") for page in document]


def ingest_version(session, version_id, path):
    """Replace chunk spans before fact review, within the caller's transaction."""
    rows = list(chunks(page_texts(path), version_id))
    session.exec(delete(Chunk).where(Chunk.version_id == version_id))
    session.add_all(rows)
    session.flush()
    return rows
