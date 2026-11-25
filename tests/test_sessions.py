from itertools import combinations
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from app import sessions
from app.db import PairResult
from app.ingest.load import load_directory
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_incremental_pairs_cards_and_download(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES, confirm_demo=True)
    engine = create_engine("sqlite:///advisor.db")
    calls = []
    original = sessions.decide
    def record(a,b,*args):
        calls.append({a.ref.code,b.ref.code})
        return original(a,b,*args)
    monkeypatch.setattr(sessions,"decide",record)
    with TestClient(create_app()) as client:
        sid = client.post("/sessions",json={"name":"Fixture storage review"}).json()["id"]
        url = f"/sessions/{sid}"
        codes = ["ASTER-100","BOREAL-200","CIRRUS-300","DAHLIA-400","EMBER-500"]
        for code in codes:
            assert client.post(url+"/products",json={"code":code}).status_code == 200
        assert len(calls) == 10
        with Session(engine) as db:
            before = {r.id:r.model_dump() for r in db.exec(select(PairResult))}
        calls.clear()
        report = client.post(url+"/products",json={"code":"FERN-600"}).json()
        assert calls == [{code,"FERN-600"} for code in codes]
        with Session(engine) as db:
            rows = db.exec(select(PairResult)).all()
            assert len(rows) == 15
            assert all(r.model_dump() == before[r.id] for r in rows if r.id in before)
            r2 = next(r for r in rows if r.rule_id == "R2")
            assert r2.a_version_id < r2.b_version_id
        conflicts = {tuple(sorted([p["a"]["code"],p["b"]["code"]])) for p in report["pairs"] if p["verdict"] == "CONFLICT"}
        assert conflicts == {("ASTER-100","BOREAL-200"),("DAHLIA-400","EMBER-500")}
        assert not report["abstained"] and all(p["abstained"] == (p["verdict"]=="UNRESOLVED") for p in report["pairs"])
        assert {e["section"] for e in next(p for p in report["pairs"] if p["rule_id"]=="R1")["evidence"]} == {"7","10.5"}
        for c in report["chemicals"]:
            assert c["product"]["name"] and c["product"]["code"] and c["sds_version"]
            assert c["ppe"] == [] and not any(c["emergency"].values())
            assert {g["section"] for g in c["to_verify"] if g["reason"]=="SDS_SAYS_NO_DATA"} == {"4","5","6","8"}
        matrix = client.get(url)
        assert matrix.status_code == 200 and matrix.text.count('class="badge CONFLICT"') == 4
        pair = client.get(f"{url}/pairs/{r2.id}").text
        assert "H272" in pair and "strong oxidising agents" in pair and "What you need to verify" in pair
        assert "Section 8" in client.get(url+"/chemicals/ASTER-100").text
        assert client.get(url+"/download").json() == report
        assert client.post(url+"/products",json={"code":"FERN-600"}).json() == report
        assert len(calls)==5
        assert client.post(url+"/products",json={"code":"MISSING"}).status_code == 422
        assert client.get(f"/sessions/999/pairs/{r2.id}").status_code == 404
        empty = client.post("/sessions",json={}).json()["id"]
        assert client.get(f"/sessions/{empty}/download").json()["abstained"] is False
        for code in ("BOREAL-200","CIRRUS-300"):
            client.post(f"/sessions/{empty}/products",json={"code":code})
        assert client.get(f"/sessions/{empty}/download").json()["abstained"] is True
