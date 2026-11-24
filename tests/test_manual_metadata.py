import pymupdf
from sqlmodel import Session, create_engine, select
from app.db import Chunk, Product, SDSVersion
from app.ingest.load import load_directory


def test_manual_metadata_does_not_interrupt_other_imports(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    inputs = {
        "a.pdf": ("Product name: Missing code", "2026-11-20"),
        "b.pdf": ("Product name: Ambiguous date\nProduct code: BAD", "03/11/2026"),
        "c.pdf": ("Product name: One\nTrade name: Two\nProduct code: TWO", "2026-11-20"),
        "d.pdf": ("Trade name: Valid\nProduct code: GOOD", "2026-11-20"),
    }
    for file, (fields, revision) in inputs.items():
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((35, 50), f"Revised: {revision}\nSECTION 1: Identification\n{fields}")
            pdf.save(tmp_path / file)
    load_directory(tmp_path)
    messages = set(capsys.readouterr().out.splitlines())
    assert messages == {"a.pdf: needs manual metadata (missing product_code)",
                        "b.pdf: needs manual metadata (missing revision_date)",
                        "c.pdf: needs manual metadata (missing product_name)", "d.pdf: active"}
    with Session(create_engine("sqlite:///" + str(tmp_path / "advisor.db"))) as db:
        assert [p.code for p in db.exec(select(Product))] == ["GOOD"]
        version = db.exec(select(SDSVersion)).one()
        assert {c.version_id for c in db.exec(select(Chunk))} == {version.id}
