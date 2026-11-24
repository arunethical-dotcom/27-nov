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


def test_revision_alone_requires_confirmation(tmp_path):
    import shutil
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "fixtures" / "SDS-Aster-100-rev2.pdf", tmp_path)
    subprocess.run([sys.executable, "-m", "app.ingest.load", "."], cwd=tmp_path,
                   env=dict(os.environ, PYTHONPATH=str(root)), check=True)
    with Session(create_engine("sqlite:///" + str(tmp_path / "advisor.db"))) as db:
        version = db.exec(select(SDSVersion)).one()
        assert (version.version_no, version.status) == (2, "pending")
