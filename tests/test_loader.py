import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine
from sqlmodel import Session, select
from app.db import Chunk, Product, SDSVersion


def test_cli_loads_seven_versions_and_skips_duplicate_hashes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    command = [sys.executable, "-m", "app.ingest.load", str(root / "fixtures")]
    def load():
        return subprocess.run(command, cwd=tmp_path, env=env, capture_output=True,
                              text=True, check=True).stdout

    first = load()
    assert "pending: needs user confirmation" in first
    with Session(create_engine("sqlite:///" + str(tmp_path / "advisor.db"))) as db:
        products = db.exec(select(Product)).all()
        versions = db.exec(select(SDSVersion)).all()
        assert len(products) == 6 and len(versions) == 7
        aster = next(p for p in products if p.code == "ASTER-100")
        assert sorted((v.version_no, str(v.revision_date), v.status) for v in versions
                      if v.product_id == aster.id) == [
                          (1, "2026-11-01", "active"), (2, "2026-11-20", "pending")]
        assert all(v.uploaded_by == "demo" for v in versions)
        assert sum(v.status == "active" for v in versions) == 6
        before = [(r.id, r.version_id) for r in db.exec(select(Chunk)).all()]
        assert {v.id for v in versions} == {v for _, v in before}
        assert load().count("duplicate") == 7
        assert [(r.id, r.version_id) for r in db.exec(select(Chunk)).all()] == before


def test_database_order_and_older_or_same_date(tmp_path, monkeypatch, capsys):
    import pymupdf
    from app.ingest.load import load_directory

    monkeypatch.chdir(tmp_path)
    inbox = tmp_path / "incoming"
    inbox.mkdir()
    uploads = [("supplier-rev99.pdf", "2026-11-20", "active"),
               ("new-delivery.pdf", "2026-11-01", "older or same revision date: needs user confirmation"),
               ("same-date.pdf", "2026-11-20", "older or same revision date: needs user confirmation"),
               ("another.pdf", "2026-11-21", "pending: needs user confirmation")]
    for filename, revision, message in uploads:
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 55), f"Revision date: {revision}\nSECTION 1: Identification\n"
                                       f"Product name: Aster 100\nProduct code: ASTER-100\n{filename}")
            pdf.save(inbox / filename)
        load_directory(inbox)
        assert f"{filename}: {message}" in capsys.readouterr().out.splitlines()
    with Session(create_engine("sqlite:///" + str(tmp_path / "advisor.db"))) as db:
        versions = db.exec(select(SDSVersion).order_by(SDSVersion.id)).all()
        assert [(v.version_no, v.status) for v in versions] == [
            (1, "active"), (2, "pending"), (3, "pending"), (4, "pending")]
