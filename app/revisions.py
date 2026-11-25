"""SDS revisions: stage uploads, preview a replacement, then replace or discard.

A preview runs the real replacement inside a transaction and rolls it back, so
the rule engine and citation checks are exactly the ones a confirmed
replacement uses. Nothing is saved until a named user confirms.
"""
import difflib
import re
from datetime import date
from hashlib import sha256

import pymupdf
from sqlalchemy import text
from sqlmodel import delete, func, select

from app.advise import Sheet, decide
from app.chunking import chunks, document_text
from app.db import (ChangeReport, Chunk, Fact, PairResult, Product, SDSVersion,
                    Session as WorkSession, SessionProduct, now)
from app.facts import confirm, extract, mapping
from app.ingest.metadata import extract_metadata
from app.retrieval.search import Search

DIFF_SECTIONS = ("2", "7", "8", "10.5", "14")
SECTION_NAMES = {"2": "Hazard identification", "7": "Handling and storage",
                 "8": "Exposure controls / PPE", "10.5": "Incompatible materials",
                 "14": "Transport information"}
MAX_UPLOAD = 20 * 1024 * 1024


class RevisionError(ValueError):
    pass


def label(version):
    revised = version.revision_date.isoformat() if version.revision_date else "date unknown"
    return f"Version {version.version_no} · revised {revised}"


def version_labels(db):
    return {v.id: label(v) for v in db.exec(select(SDSVersion))}


def pending_versions(db):
    rows = db.exec(select(SDSVersion, Product).join(Product)
                   .where(SDSVersion.status == "pending").order_by(SDSVersion.id)).all()
    return [dict(id=v.id, name=p.name, code=p.code, label=label(v)) for v, p in rows]


# ---------------------------------------------------------------- uploads

def pdf_pages(data):
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            return [page.get_text("text") for page in document]
    except Exception as exc:  # PyMuPDF raises several types for damaged files
        raise RevisionError("The file could not be read as a PDF.") from exc


def match_product(db, code, name, cas):
    """Identity by product code, then exact name, then a shared CAS number."""
    product = db.exec(select(Product).where(Product.code == code)).first()
    if product:
        return product
    for candidate in db.exec(select(Product)):
        if candidate.name.casefold() == name.casefold():
            return candidate
        if cas and set(cas) & set(candidate.cas_numbers or []):
            return candidate
    return None


def stage_upload(db, data, filename, uploaded_by):
    """Return (outcome, version_id or None, message). Outcomes: duplicate,
    needs_metadata, new (active, facts await review) or pending (awaits replace)."""
    if not data:
        raise RevisionError("Choose a PDF file to upload.")
    if len(data) > MAX_UPLOAD:
        raise RevisionError("The file is larger than 20 MB.")
    digest = sha256(data).hexdigest()
    existing = db.exec(select(SDSVersion).where(SDSVersion.file_hash == digest)).first()
    if existing:
        return "duplicate", existing.id, f"{filename}: already uploaded."
    pages = pdf_pages(data)
    rows = list(chunks(pages, 0))
    metadata = extract_metadata(rows)
    fields = {}
    for key in ("product_code", "revision_date", "product_name"):
        candidates = metadata[key]
        values = {item.value for item in candidates}
        if len(values) != 1 or any(item.ambiguous for item in candidates):
            return "needs_metadata", None, f"{filename}: needs manual metadata (missing {key.replace('_', ' ')})."
        fields[key] = values.pop()
    cas = sorted({v.value for v in metadata["cas_numbers"]})
    product = match_product(db, fields["product_code"], fields["product_name"], cas)
    new_product = product is None
    if new_product:
        product = Product(code=fields["product_code"], name=fields["product_name"], cas_numbers=cas)
        db.add(product)
        db.flush()
    previous = db.exec(select(func.max(SDSVersion.version_no))
                       .where(SDSVersion.product_id == product.id)).one() or 0
    version = SDSVersion(product_id=product.id, version_no=previous + 1,
                         revision_date=date.fromisoformat(fields["revision_date"]),
                         file_hash=digest, status="active" if new_product else "pending",
                         uploaded_by=uploaded_by, document_text=document_text(pages))
    db.add(version)
    db.flush()
    for row in rows:
        row.version_id = version.id
    db.add_all(rows)
    db.flush()
    if new_product:
        extract(db, version)
        return "new", version.id, f"{product.name}: new product added. Review its facts."
    return "pending", version.id, f"{product.name}: new version staged. Review before replacing."


def discard(db, version_id):
    version = db.get(SDSVersion, version_id)
    if version is None or version.status != "pending":
        raise RevisionError("Only a pending version can be discarded.")
    db.exec(delete(Fact).where(Fact.version_id == version.id))
    db.exec(delete(Chunk).where(Chunk.version_id == version.id))
    db.delete(version)
    db.flush()


# ---------------------------------------------------------------- comparison

def words(text):
    return re.findall(r"\w+(?:[-‐]\w+)*|[^\w\s]|\s+", text)


def diff_segments(old, new):
    """Word-level diff as [op, text] with op in equal / delete / insert."""
    a, b = words(old), words(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["equal", "".join(a[i1:i2])])
            continue
        if i2 > i1:
            out.append(["delete", "".join(a[i1:i2])])
        if j2 > j1:
            out.append(["insert", "".join(b[j1:j2])])
    return out


def section_text(db, version, key):
    rows = db.exec(select(Chunk).where(Chunk.version_id == version.id, Chunk.method == "section")
                   .order_by(Chunk.start)).all()
    if key == "10.5":
        picked = [r for r in rows if r.section == "10.5"]
    else:
        picked = [r for r in rows if r.section.split(".")[0] == key]
    return "".join(r.text for r in picked).strip()


def field_diff(db, old, new):
    changed, unchanged = [], []
    for key in DIFF_SECTIONS:
        before, after = section_text(db, old, key), section_text(db, new, key)
        if before == after:
            unchanged.append(key)
        else:
            changed.append(dict(section=key, name=SECTION_NAMES[key], old=before, new=after,
                                segments=diff_segments(before, after)))
    return dict(changed=changed, unchanged=unchanged)


def evidence_keys(evidence, codes):
    """Evidence identity independent of chunk ids and of which side a sheet is on."""
    return sorted({(codes[e["side"]], e["section"], e["quote"]) for e in evidence})


def gap_keys(gaps):
    return sorted({(g["product"]["code"], g["section"], g["reason"]) for g in gaps})


def change_flag(old, new):
    """STATUS_CHANGED: verdict differs. EVIDENCE_CHANGED: same verdict, but what it
    rests on differs (the cited quotes for a decision, the gaps for UNRESOLVED)."""
    if old is None:
        return "NONE"
    if old["verdict"] != new["verdict"]:
        return "STATUS_CHANGED"
    if new["verdict"] == "UNRESOLVED":
        return "NONE" if old["gaps"] == new["gaps"] else "EVIDENCE_CHANGED"
    return "NONE" if old["evidence"] == new["evidence"] else "EVIDENCE_CHANGED"


def quote_changes(old, new):
    """Per product and section, the cited text before and after with word highlights."""
    def grouped(items):
        out = {}
        for code, section, quote in items:
            out.setdefault((code, section), []).append(quote)
        return {k: " / ".join(v) for k, v in out.items()}
    before, after = grouped(old), grouped(new)
    rows = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key, ""), after.get(key, "")
        rows.append(dict(code=key[0], section=key[1], old=a, new=b, changed=a != b,
                         segments=diff_segments(a, b)))
    return rows


# ---------------------------------------------------------------- replacement

def apply_replacement(db, version_id, user):
    """Swap the active version, re-run every saved pair of the product and record
    flags. Callers commit (Replace) or roll back (preview). Returns plain data."""
    pending = db.get(SDSVersion, version_id)
    if pending is None or pending.status != "pending":
        raise RevisionError("This version is not waiting for review.")
    product = db.get(Product, pending.product_id)
    old = db.exec(select(SDSVersion).where(SDSVersion.product_id == product.id,
                                           SDSVersion.status == "active")).first()
    if old is None:
        raise RevisionError("No active version to replace.")
    summary = dict(product=dict(code=product.code, name=product.name),
                   old=dict(id=old.id, version_no=old.version_no, label=label(old),
                            revision_date=str(old.revision_date)),
                   new=dict(id=pending.id, version_no=pending.version_no, label=label(pending),
                            revision_date=str(pending.revision_date)),
                   not_later=bool(old.revision_date and pending.revision_date
                                  and pending.revision_date <= old.revision_date),
                   fields=field_diff(db, old, pending))

    old.status = "archived"
    db.add(old)
    db.flush()
    pending.status = "active"
    db.add(pending)
    db.flush()
    confirm(db, pending, user)

    search = Search(db, None, {})
    r3 = mapping()["reactive_groups"]["enabled"]
    current = Sheet(db, product, pending)
    affected, new_rows = [], []
    memberships = db.exec(select(SessionProduct).where(SessionProduct.product_id == product.id,
                                                       SessionProduct.version_id == old.id)).all()
    for membership in memberships:
        work = db.get(WorkSession, membership.session_id)
        peers = db.exec(select(SessionProduct).where(SessionProduct.session_id == work.id,
                                                     SessionProduct.product_id != product.id)
                        .order_by(SessionProduct.product_id)).all()
        for peer in peers:
            peer_version = db.get(SDSVersion, peer.version_id)
            if peer_version is None or peer_version.status != "active":
                raise RevisionError("Another product in a saved session needs review first.")
            peer_product = db.get(Product, peer.product_id)
            sheet = Sheet(db, peer_product, peer_version)
            a, b = sorted([current, sheet], key=lambda s: s.version.id)
            pair = decide(a, b, search, r3)
            low, high = sorted([old.id, peer_version.id])
            previous = db.exec(select(PairResult).where(
                PairResult.session_id == work.id, PairResult.a_version_id == low,
                PairResult.b_version_id == high)).first()
            old_codes = {"A": product.code if low == old.id else peer_product.code,
                         "B": peer_product.code if low == old.id else product.code}
            new_codes = {"A": a.product.code, "B": b.product.code}
            before = None
            if previous is not None:
                before = dict(verdict=previous.verdict, rule=previous.rule_id,
                              evidence=evidence_keys(previous.evidence_json, old_codes),
                              gaps=gap_keys(previous.to_verify_json))
            evidence_json = [p.model_dump() for p in pair.evidence]
            gaps_json = [g.model_dump() for g in pair.to_verify]
            after = dict(verdict=pair.verdict, rule=pair.rule_id,
                         evidence=evidence_keys(evidence_json, new_codes), gaps=gap_keys(gaps_json))
            flag = change_flag(before, after)
            row = PairResult(session_id=work.id, a_version_id=a.version.id, b_version_id=b.version.id,
                             verdict=pair.verdict, rule_id=pair.rule_id, evidence_json=evidence_json,
                             to_verify_json=gaps_json, change_flag=flag,
                             previous_verdict=before["verdict"] if before else None)
            db.add(row)
            new_rows.append(row)
            affected.append(dict(
                session_id=work.id, session=work.name,
                peer=dict(code=peer_product.code, name=peer_product.name),
                before=dict(verdict=before["verdict"], rule=before["rule"]) if before else None,
                after=dict(verdict=after["verdict"], rule=after["rule"]),
                flag=flag,
                quotes=quote_changes(before["evidence"] if before else [], after["evidence"]),
                gaps_before=before["gaps"] if before else [], gaps_after=after["gaps"]))
        membership.version_id = pending.id
        db.add(membership)
    db.flush()
    summary["affected"] = affected
    summary["counts"] = {flag: sum(1 for x in affected if x["flag"] == flag)
                         for flag in ("STATUS_CHANGED", "EVIDENCE_CHANGED", "NONE")}
    return summary, new_rows


def preview(db, version_id):
    """Run the replacement, read the result, then roll everything back."""
    transaction = db.begin()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        summary, _ = apply_replacement(db, version_id, "preview")
        return summary
    finally:
        transaction.rollback()


def replace(db, version_id, user):
    """Commit a replacement and its change report. The caller owns the transaction."""
    user = (user or "").strip()
    if not user or len(user) > 120:
        raise RevisionError("Enter your name to confirm the replacement.")
    summary, rows = apply_replacement(db, version_id, user)
    report = ChangeReport(product_id=db.get(SDSVersion, version_id).product_id,
                          old_version_id=summary["old"]["id"], new_version_id=summary["new"]["id"],
                          field_diff_json=summary["fields"],
                          affected_pairs_json=summary["affected"],
                          confirmed_by=user, confirmed_at=now())
    db.add(report)
    db.flush()
    for row in rows:
        row.change_report_id = report.id
        db.add(row)
    db.flush()
    return report.id
