import pytest
from app.chunking import chunks


@pytest.mark.parametrize("ending", ["", "\n"])
def test_continued_sentence_and_section_have_exact_document_spans(ending):
    from app.chunking import document_text

    pages = ["SECTION 7: Handling\n7.2 Conditions for safe storage: Keep away from" + ending,
             "strong oxidising agents.\nSECTION 8: Exposure controls\nFirst line."]
    document = document_text(pages)
    rows = list(chunks(pages, 1))
    for row in rows:
        assert row.text == document[row.start:row.end]
        assert row.page == row.page_start
    for method in ("section", "sentence", "window"):
        row, = [r for r in rows if r.section == "7.2" and r.method == method]
        assert (row.page_start, row.page_end) == (1, 2)
        assert "Keep away from\nstrong oxidising agents." in row.text
        if method == "sentence":
            assert row.text == "Keep away from\nstrong oxidising agents."
    assert all(r.page_start == r.page_end == 2 for r in rows if r.section == "8")


def test_removed_footer_blocks_cross_page_join():
    from app.chunking import document_text

    pages = ["SECTION 7: Handling\n7.2 Storage: Keep away from\nPage 1 of 2\n",
             "strong oxidising agents."]
    rows = list(chunks(pages, 1))
    assert all(r.page_start == r.page_end for r in rows)
    assert all(r.text == document_text(pages)[r.start:r.end] for r in rows)
    assert {r.page for r in rows if r.section == "7.2"} == {1, 2}


def test_import_persists_source_and_validates_exact_spans(tmp_path, monkeypatch):
    import pymupdf
    from sqlmodel import Session, create_engine, select
    from app.chunking import document_text
    from app.db import Chunk, SDSVersion
    from app.ingest import page_texts
    from app.ingest.load import load_directory

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "two-pages.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((35, 50), "Revision date: 2026-01-01\nSECTION 1: Identification\n"
                                   "Product code: DEMO\nProduct name: Demo\nSECTION 7: Handling\n"
                                   "7.2 Conditions for safe storage: Keep away from")
        pdf.new_page().insert_text((35, 50), "strong oxidising agents.")
        pdf.save(path)
    load_directory(tmp_path)
    with Session(create_engine("sqlite:///advisor.db")) as db:
        version = db.exec(select(SDSVersion)).one()
        assert version.document_text == document_text(page_texts(path))
        rows = db.exec(select(Chunk)).all()
        assert all(r.text == version.document_text[r.start:r.end] for r in rows)
        assert any(r.method == "sentence" and (r.page_start, r.page_end) == (1, 2) for r in rows)

        assert all(r.matches_source(version.document_text) for r in rows)
        rows[0].text += "!"
        assert not rows[0].matches_source(version.document_text)
