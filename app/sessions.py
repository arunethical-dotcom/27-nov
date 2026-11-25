"""Transactional session membership and immutable, version-pinned pair results."""
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.exceptions import HTTPException

from app.advise import Sheet, decide
from app.db import PairResult, Product, SDSVersion, Session as WorkSession, SessionProduct
from app.facts import mapping
from app.retrieval.search import Search
from app.schemas import Advisory, Evidence, Gap, Pair


def workspace(db, sid):
    session = db.get(WorkSession, sid)
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def members(db, sid):
    return db.exec(select(SessionProduct).where(SessionProduct.session_id == sid)
                   .order_by(SessionProduct.product_id)).all()


def add_product(db: Session, sid, code, *, locked=False):
    if not locked:
        db.execute(text("BEGIN IMMEDIATE"))
    workspace(db, sid)
    search = Search(db, None, {})
    if code not in search.products:
        raise HTTPException(422, "Choose a product with an active SDS.")
    product = search.products[code]
    existing = members(db, sid)
    if any(m.product_id == product.id for m in existing):
        return
    current = Sheet(db, product, db.get(SDSVersion, search.versions[code]))
    for m in existing:
        version = db.get(SDSVersion, m.version_id)
        if version is None or version.status != "active":
            raise HTTPException(409, "A saved SDS version requires review.")
        peer = Sheet(db, db.get(Product, m.product_id), version)
        a,b = sorted([current, peer], key=lambda sheet: sheet.version.id)
        pair = decide(a, b, search, mapping()["reactive_groups"]["enabled"])
        db.add(PairResult(session_id=sid, a_version_id=a.version.id, b_version_id=b.version.id,
                          verdict=pair.verdict, rule_id=pair.rule_id,
                          evidence_json=[p.model_dump() for p in pair.evidence],
                          to_verify_json=[g.model_dump() for g in pair.to_verify]))
    db.add(SessionProduct(session_id=sid, product_id=product.id, version_id=current.version.id))


def read_session(db, sid):
    """Read saved decisions. Validate their source passages on every display."""
    work = workspace(db, sid)
    search = Search(db, None, {})
    cards, sheets = [], {}
    for m in members(db,sid):
        version = db.get(SDSVersion,m.version_id)
        if version is None or version.status != "active":
            raise HTTPException(409, "A saved SDS version requires review.")
        sheet = Sheet(db,db.get(Product,m.product_id),version)
        sheets[version.id] = sheet
        cards.append(sheet.card(search))
    pairs, links = [], {}
    result_rows = db.exec(select(PairResult).where(PairResult.session_id == sid).order_by(PairResult.id))
    for row in result_rows:
        # Rows pinned to an archived version stay as history; show current ones only.
        if row.a_version_id not in sheets or row.b_version_id not in sheets:
            continue
        a,b = sheets[row.a_version_id],sheets[row.b_version_id]
        evidence = []
        gaps = [Gap.model_validate(g) for g in row.to_verify_json]
        invalid = []
        for raw in row.evidence_json:
            p = Evidence.model_validate(raw)
            sheet = a if p.side == "A" else b
            sources = [*sheet.confirmed, *sheet.notes]
            if any(f.chunk_id == p.chunk_id and (source := sheet.passage(f)) is not None
                   and source.model_dump() == p.model_dump(exclude={"side"}) for f in sources):
                evidence.append(p)
            else:
                invalid.append(Gap(product=sheet.ref, section=p.section, reason="NOT_LOCATED"))
        verdict,rule = row.verdict,row.rule_id
        if (verdict == "CONFLICT" and (not evidence or
            (rule in {"R2","R3"} and (invalid or {e.side for e in evidence} != {"A","B"})))) or (
                verdict == "NO_CONFLICT_FOUND" and (invalid or not evidence)):
            verdict,rule = "UNRESOLVED","R4"
            gaps.extend(invalid)
        pair = Pair(a=a.ref,b=b.ref,a_version_id=a.version.id,b_version_id=b.version.id,
                    abstained=verdict=="UNRESOLVED",verdict=verdict,rule_id=rule,evidence=evidence,to_verify=gaps,
                    change_flag=row.change_flag or "NONE",previous_verdict=row.previous_verdict,
                    change_report_id=row.change_report_id)
        pairs.append(pair)
        links[tuple(sorted((a.ref.code,b.ref.code)))] = (row.id,pair)
    report = Advisory(session_id=str(sid),chemicals=cards,pairs=pairs,
                      abstained=bool(pairs) and all(p.abstained for p in pairs))
    return work,report,links
