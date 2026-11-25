from pathlib import Path
import re

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from app.advise import advise
from app.db import Chunk, Fact, Product, SDSVersion
from app.facts import propose
from app.ingest.load import load_directory
from app.main import create_app
from app.retrieval.search import Search

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES, confirm_demo=True)
    with Session(create_engine("sqlite:///advisor.db")) as session:
        yield session


def test_demo_steps_and_both_citations(db, monkeypatch):
    gold = yaml.safe_load((FIXTURES / "expected.yaml").read_text())
    products = db.exec(select(Product).order_by(Product.id)).all()
    # Simulate retrieval missing every rule-bearing passage.
    monkeypatch.setattr(Search, "rank", lambda *args, **kwargs: [])
    report = advise(db, [p.code for p in products])
    pairs = {f"{p.a.name} + {p.b.name}":p for p in report.pairs}
    assert len(pairs) == 15
    for label, expected in gold["step_1"].items():
        assert (pairs[label].verdict, pairs[label].rule_id) == (expected["verdict"], expected["rule"])
    for label,p in pairs.items():
        if label in gold["step_1"]:
            continue
        wanted = gold["step_2"].get(label)
        if wanted:
            assert (p.verdict,p.rule_id) == (wanted["verdict"],wanted["rule"])
        else:
            assert p.verdict in gold["step_2"]["other_new_pairs_allowed"]
    r2 = pairs["Dahlia 400 + Ember 500"]
    assert [(e.side, e.section) for e in r2.evidence] == [("A","7"),("B","2")]
    for e, token in zip(r2.evidence, gold["step_2"]["Dahlia 400 + Ember 500"]["evidence"]):
        assert token.lower() in e.quote.lower()
    for p in report.pairs:
        for e in p.evidence:
            c = db.get(Chunk, e.chunk_id)
            v = db.get(SDSVersion, c.version_id)
            assert v.status == "active" and c.matches_source(v.document_text) and e.quote in c.text
    assert not re.search(r"\b(safe|compatible)\b", report.model_dump_json(), re.I)
    single = advise(db, ["ASTER-100"])
    assert single.pairs == [] and single.chemicals[0].storage_notes
    assert set(single.chemicals[0].emergency) == {"4","5","6"} and not single.chemicals[0].ppe
    group = advise(db, ["EMBER-500","FERN-600"], enable_r3=True).pairs[0]
    assert group.rule_id == "R3" and {e.side for e in group.evidence} == {"A","B"}


def test_tampered_r1_and_half_proven_r2_abstain(db):
    for f in db.exec(select(Fact).where(Fact.type.in_(["restriction_name","h_code","hazard_class"]))):
        f.quote = "altered"
    db.flush()
    for codes in [["ASTER-100","BOREAL-200"],["DAHLIA-400","EMBER-500"]]:
        report = advise(db,codes)
        assert report.abstained and report.pairs[0].verdict == "UNRESOLVED"
        assert report.pairs[0].to_verify
        assert "altered" not in report.model_dump_json()


def test_review_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES)
    engine = create_engine("sqlite:///advisor.db")
    with Session(engine) as session:
        assert advise(session, ["ASTER-100","BOREAL-200"]).pairs[0].verdict == "UNRESOLVED"
    with TestClient(create_app()) as client:
        assert "Awaiting review" in client.get("/versions/1/facts").text
        assert client.post("/versions/1/facts/confirm", json={"user":" "}).status_code == 422
        assert client.post("/versions/1/facts/confirm", json={"user":"Reviewer"}).status_code == 200
        assert client.post("/versions/2/facts/confirm", json={"user":"Reviewer"}).status_code == 404
    with Session(engine) as session:
        result = advise(session, ["ASTER-100","BOREAL-200"])
        assert result.pairs[0].rule_id == "R1"
        assert {f.confirmed_by for f in session.exec(select(Fact).where(Fact.version_id == 1))} == {"Reviewer"}


def test_r5_requires_confirmed_explicit_complete_storage_data(tmp_path, monkeypatch):
    import pymupdf
    monkeypatch.chdir(tmp_path)
    for code in ["AAA","BBB"]:
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40,50),
                f"Revision date: 2026-01-01\nSECTION 1: Identification\nProduct code: {code}\nProduct name: {code}\n"
                "SECTION 2: Hazards\nHazard classification: Flammable liquid.\n"
                "SECTION 7: Storage\nNo known storage restrictions.\nSECTION 10: Stability\n10.5 Incompatible materials: None known.")
            pdf.save(f"{code}.pdf")
    load_directory(tmp_path, confirm_demo=True)
    with Session(create_engine("sqlite:///advisor.db")) as session:
        pair = advise(session,["AAA","BBB"]).pairs[0]
        assert (pair.verdict, pair.rule_id) == ("NO_CONFLICT_FOUND","R5")
        assert {(e.side,e.section) for e in pair.evidence} >= set((s,n) for s in "AB" for n in ("7","10.5"))


@pytest.mark.parametrize("alias", ["Solvent Two","B-2","Test alias","64-17-5"])
def test_named_restrictions_use_all_aliases(alias):
    product = Product(id=2,name="Solvent Two",code="B-2",synonyms=["Test alias"],cas_numbers=["64-17-5"])
    c = Chunk(id=1,version_id=1,section="7",method="section",page=1,start=100,
              text=f"Keep away from {alias}.")
    f, = propose(c,[product],1)
    assert (f.type,f.value,f.start,f.quote) == ("restriction_name","2",100,c.text)


@pytest.mark.parametrize("section,raw,kind", [
    ("8", "Wear eye protection. Wear gloves.", "ppe"),
    ("4", "Rinse eyes. Seek medical advice.", "emergency"),
    ("5", "Use water spray.", "emergency"),
    ("6", "Ventilate the area.", "emergency"),
])
def test_instruction_sentences_keep_exact_offsets(section, raw, kind):
    c = Chunk(id=11,version_id=1,section=section,method="section",page=1,start=20,text=raw)
    result = list(propose(c, [], 1))
    assert result and all(f.type == kind and f.chunk_id == 11 for f in result)
    assert all(raw[f.start-20:f.end-20] == f.quote for f in result)


def test_extract_class_outside_rule_map():
    raw = "Hazard classification: Acute toxicity, Category 3."
    c = Chunk(id=8, version_id=1, section="2", method="section", page=1, start=200, text=raw)
    f, = propose(c, [], 1)
    assert (f.type, f.value) == ("hazard_class", "Acute toxicity, Category 3.")
