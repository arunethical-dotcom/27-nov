"""Phase 5: ask before replacing, preview without saving, change report and flags."""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from app.db import ChangeReport, Fact, PairResult, SDSVersion
from app.ingest.load import load_directory
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
REV2 = FIXTURES / "SDS-Aster-100-rev2.pdf"
CODES = {"Aster 100": "ASTER-100", "Boreal 200": "BOREAL-200", "Cirrus 300": "CIRRUS-300"}


@pytest.fixture
def app_with_session(tmp_path, monkeypatch):
    """Six active demo products (no rev2) and the fixture session, as in the demo."""
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES, confirm_demo=True, exclude=["*rev2*"])
    engine = create_engine("sqlite:///advisor.db")
    with TestClient(create_app()) as client:
        r = client.post("/sessions", data={"name": "Fixture storage review", "demo": "fixtures"},
                        follow_redirects=False)
        sid = int(r.headers["location"].rsplit("/", 1)[1])
        yield client, engine, sid


def upload(client, path, user="Arun"):
    return client.post("/upload", files={"file": (path.name, path.read_bytes(), "application/pdf")},
                       data={"user": user}, follow_redirects=False)


def snapshot(engine):
    with Session(engine) as db:
        return (sorted((v.id, v.status) for v in db.exec(select(SDSVersion))),
                len(db.exec(select(PairResult)).all()),
                sum(1 for f in db.exec(select(Fact)) if f.confirmed_by),
                len(db.exec(select(ChangeReport)).all()))


def aster_pairs(client, sid):
    pairs = client.get(f"/sessions/{sid}/download").json()["pairs"]
    out = {}
    for p in pairs:
        codes = {p["a"]["code"], p["b"]["code"]}
        if "ASTER-100" in codes:
            out[(codes - {"ASTER-100"}).pop()] = p
    return out, pairs


def test_upload_stages_pending_and_preview_saves_nothing(app_with_session):
    client, engine, sid = app_with_session
    r = upload(client, REV2)
    assert r.status_code == 303 and r.headers["location"].endswith("/preview")
    before = snapshot(engine)
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Replace Aster 100 version 1 (2026-11-01) with version 2 (2026-11-20)?" in page.text
    assert "<ins> or Cirrus 300 (CIRRUS-300)</ins>" in page.text
    assert "1 status changed · 1 evidence changed · 3 no change" in page.text
    # A preview must not change anything, however many times it is opened.
    client.get(r.headers["location"])
    assert snapshot(engine) == before
    with Session(engine) as db:
        statuses = {v.version_no: v.status for v in db.exec(select(SDSVersion).where(SDSVersion.product_id == 1))}
    assert statuses == {1: "active", 2: "pending"}
    assert "SDS updates waiting for review" in client.get(f"/sessions/{sid}").text


def test_replace_matches_expected_step_3(app_with_session):
    client, engine, sid = app_with_session
    expected = yaml.safe_load((FIXTURES / "expected.yaml").read_text())["step_3"]
    vid = int(upload(client, REV2).headers["location"].split("/")[2])
    before_pairs, _ = aster_pairs(client, sid)

    assert client.post(f"/versions/{vid}/replace", data={"user": "  "}).status_code == 422
    r = client.post(f"/versions/{vid}/replace", data={"user": "Arun"}, follow_redirects=False)
    assert r.status_code == 303
    report_page = client.get(r.headers["location"])
    assert "Replaced by <strong>Arun</strong>" in report_page.text

    after, pairs = aster_pairs(client, sid)
    assert len(pairs) == 15
    assert all(p["a"]["code"] != p["b"]["code"] for p in pairs)  # no Aster + Aster
    for name, spec in expected.items():
        if " + " not in name:
            continue
        peer = CODES[name.split(" + ")[1]]
        assert before_pairs[peer]["verdict"] == spec["before"]
        assert after[peer]["verdict"] == spec["after"]
        assert after[peer]["change_flag"] == spec["change_flag"]
    others = set(after) - {"BOREAL-200", "CIRRUS-300"}
    assert others and all(after[c]["change_flag"] == expected["other_aster_pairs"] for c in others)
    assert after["CIRRUS-300"]["previous_verdict"] == "UNRESOLVED"
    quotes = {e["quote"] for e in after["CIRRUS-300"]["evidence"]}
    assert any("Cirrus 300 (CIRRUS-300)" in q for q in quotes)

    with Session(engine) as db:
        versions = db.exec(select(SDSVersion).where(SDSVersion.product_id == 1)).all()
        assert sorted((v.version_no, v.status) for v in versions) == [(1, "archived"), (2, "active")]
        report = db.exec(select(ChangeReport)).one()
        assert report.confirmed_by == "Arun"
        assert {a["flag"] for a in report.affected_pairs_json} == {"STATUS_CHANGED", "EVIDENCE_CHANGED", "NONE"}
        assert [c["section"] for c in report.field_diff_json["changed"]] == ["7", "10.5"]
        assert all(r.change_report_id == report.id for r in db.exec(select(PairResult))
                   if r.change_flag != "NONE")

    matrix = client.get(f"/sessions/{sid}").text
    assert "status changed" in matrix and "evidence changed" in matrix
    assert "it is not a safe result" in matrix
    pair_page = client.get(f"/sessions/{sid}/pairs/{_row_id(engine, 'EVIDENCE_CHANGED')}").text
    assert "review required" in pair_page and "Version 2 · revised 2026-11-20" in pair_page
    assert "SDS version ID" not in pair_page
    assert "Arun" in client.get("/change-reports").text


def _row_id(engine, flag):
    with Session(engine) as db:
        return db.exec(select(PairResult.id).where(PairResult.change_flag == flag)).first()


def test_keep_current_discards_the_upload(app_with_session):
    client, engine, sid = app_with_session
    vid = int(upload(client, REV2).headers["location"].split("/")[2])
    r = client.post(f"/versions/{vid}/discard", follow_redirects=False)
    assert r.status_code == 303
    with Session(engine) as db:
        assert db.get(SDSVersion, vid) is None
        assert db.exec(select(SDSVersion).where(SDSVersion.product_id == 1)).one().status == "active"
    assert client.get(f"/versions/{vid}/preview").status_code == 404
    assert client.post(f"/versions/{vid}/replace", data={"user": "Arun"}).status_code == 422
    assert len(client.get(f"/sessions/{sid}/download").json()["pairs"]) == 15


def test_duplicate_and_unreadable_uploads(app_with_session):
    client, _, _ = app_with_session
    dup = upload(client, FIXTURES / "SDS-Boreal-200.pdf")
    assert dup.status_code == 409 and "already uploaded" in dup.text
    bad = client.post("/upload", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 422 and "could not be read" in bad.text


def test_new_product_upload_goes_to_fact_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app()) as client:
        r = upload(client, FIXTURES / "SDS-Fern-600.pdf")
        assert r.status_code == 303 and r.headers["location"].endswith("/facts")
        assert client.get(r.headers["location"]).status_code == 200


def test_cli_staged_revision_can_be_previewed_and_replaced(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_directory(FIXTURES, confirm_demo=True)
    with TestClient(create_app()) as client:
        pending = client.get("/upload").text
        assert "Aster 100 · Version 2 · revised 2026-11-20" in pending
        engine = create_engine("sqlite:///advisor.db")
        with Session(engine) as db:
            vid = db.exec(select(SDSVersion.id).where(SDSVersion.status == "pending")).one()
        assert "0 status changed · 0 evidence changed · 0 no change" in client.get(f"/versions/{vid}/preview").text
        assert client.post(f"/versions/{vid}/replace", data={"user": "Arun"},
                           follow_redirects=False).status_code == 303
