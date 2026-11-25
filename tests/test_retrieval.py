from itertools import combinations
from pathlib import Path

import numpy as np
import pytest
import yaml
from sqlmodel import Session, create_engine, select

from app.db import Chunk, SDSVersion
from app.ingest.load import load_directory
from app.retrieval.index import build_index, active_chunks, load_index
from app.retrieval.search import Search, tokens, rrf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_chemical_tokens_and_reciprocal_ranks():
    assert tokens("64-17-5 ASTER-100 UN 1234 H272 UN1234") == [
        "64-17-5", "aster-100", "un1234", "h272", "un1234"]
    assert rrf([[9, 3], [3, 8]]) == [3, 9, 8]


class TestEncoder:
    def encode(self, texts):
        return np.ones((len(texts), 384), np.float32) / np.sqrt(384)


def test_active_pair_scope_and_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES)
    with Session(create_engine("sqlite:///advisor.db")) as db:
        rows = active_chunks(db)
        path = tmp_path / "test.npy"
        build_index(rows, TestEncoder(), path)
        vectors = load_index(path, rows)
        engine = Search(db, TestEncoder(), vectors)
        pairs = list(combinations(sorted(engine.products), 2))
        gold = yaml.safe_load((FIXTURES.parent / "eval/retrieval_gold.yaml").read_text())
        assert {tuple(pair["products"]) for pair in gold["pairs"]} == set(pairs)
        pending = db.exec(select(SDSVersion).where(SDSVersion.status == "pending")).one()
        pending_ids = set(db.exec(select(Chunk.id).where(Chunk.version_id == pending.id)))
        assert pending_ids.isdisjoint(vectors)
        queries = engine.queries("EMBER-500")
        assert "H272" in queries and any("Oxidising liquid" in q for q in queries)
        for mode in ("bm25", "vector", "hybrid"):
            sides = engine.pair("ASTER-100", "BOREAL-200", "section", mode)
            assert len(sides) == 2
            for code, ids in sides.items():
                assert ids and pending_ids.isdisjoint(ids)
                assert all(engine.rows[i].version_id == engine.versions[code] for i in ids)
        for purpose, allowed in [("ppe", {"8"}), ("emergency", {"4","5","6"})]:
            ids = engine.rank("ASTER-100", ["No data available"], "section", "hybrid", purpose)
            assert ids and all(engine.rows[i].section in allowed for i in ids)
        rows[0].text += " changed"
        with pytest.raises(ValueError, match="rebuild"):
            load_index(path, rows)


def test_metrics_count_distinct_passages_and_first_relevant_rank():
    from types import SimpleNamespace
    from eval.run import metrics

    rows = {i: SimpleNamespace(section="7", text=t) for i, t in enumerate(
        ["irrelevant", "first passage", "first passage", "x", "y", "second passage"])}
    assert metrics(list(rows), [("7", "first passage"), ("7", "second passage")], rows) == (0.5, 0.5)


def test_gold_passages_exist_and_named_restriction_ranks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES)
    with Session(create_engine("sqlite:///advisor.db")) as db:
        search = Search(db, None, {})
        gold = yaml.safe_load((FIXTURES.parent / "eval/retrieval_gold.yaml").read_text())
        for code, section, quote in gold["passages"].values():
            for method in ("section", "window", "sentence"):
                assert any(row.method == method and row.section == section and quote in row.text
                           and row.version_id == search.versions[code] for row in search.rows.values())
        ids = search.pair("ASTER-100", "BOREAL-200", "section", "bm25")["ASTER-100"][:5]
        assert {"7", "10.5"} <= {search.rows[i].section for i in ids}
        version = db.get(SDSVersion, search.versions["ASTER-100"])
        version.status = "archived"
        db.add(version)
        db.flush()
        with pytest.raises(ValueError, match="Active SDS changed"):
            search.pair("ASTER-100", "BOREAL-200", "section", "bm25")
