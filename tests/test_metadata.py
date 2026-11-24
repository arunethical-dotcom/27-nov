from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from app.ingest import ingest_version, page_texts


@pytest.mark.parametrize("path", sorted(Path("fixtures").glob("*.pdf")), ids=lambda p:p.stem)
def test_fixture_metadata(path):
    from app.ingest.metadata import extract_metadata

    pages = page_texts(path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        rows = ingest_version(db, 1, path)
        assert any(r.section == "0" and r.method == "section" for r in rows)
        data = extract_metadata(rows)
        code = path.stem.removeprefix("SDS-").removesuffix("-rev2").upper()
        assert data["product_code"][0].value == code
        assert data["product_name"][0].value == code.replace("-", " ").title()
        assert data["revision_date"][0].value == ("2026-11-20" if "rev2" in path.stem else "2026-11-01")
        assert data["cas_numbers"] == []
        for values in data.values():
            for value in values:
                chunk = next(r for r in rows if r.id == value.chunk_id)
                assert chunk.start <= value.start < value.end <= chunk.end
                assert pages[chunk.page-1][value.start:value.end] == value.value


def test_multiple_cas_in_identification_only():
    from app.db import Chunk
    from app.ingest.metadata import extract_metadata

    text = "CAS: 64-17-5 and 67-64-1"
    rows = [Chunk(id=i, version_id=1, method="section", section=section,
                  start=12, end=12+len(text), page=1, text=text)
            for i, section in enumerate(["1.1", "2"], 1)]
    values = extract_metadata(rows)["cas_numbers"]
    assert [(v.value, v.chunk_id, v.start) for v in values] == [
        ("64-17-5", 1, 17), ("67-64-1", 1, 29)]
