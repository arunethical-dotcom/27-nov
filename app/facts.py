"""Deterministic proposals. Spans use absolute offsets into the stored SDS."""
import re
from functools import lru_cache
from pathlib import Path

import yaml
from sqlmodel import select
from app.chunking import sentence_spans
from app.db import Chunk, Fact, Product, SDSVersion, now

MISSING = re.compile(r"\b(?:no data available|data unavailable|not disclosed|not assessed|undisclosed)\b", re.I)
RESTRICT = re.compile(r"\b(?:do not store|must not be stored|keep away from|avoid contact with|incompatible with|store separately from)\b", re.I)
NEGATIVE = re.compile(r"\b(?:not incompatible|no incompatibilit|no known incompatibilit|not required to|need not|no need to)\b", re.I)
FORBIDDEN = re.compile(r"\b(?:safe|compatible)\b", re.I)
CLEAR = re.compile(r"(?:none known|no known incompatible materials|no known storage (?:restrictions|incompatibilities))[.!]?", re.I)


@lru_cache
def mapping():
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "rules/class_map.yaml").read_text())


def mentions(phrase, text):
    return bool(phrase and re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", text, re.I))


def passages(chunk):
    body = chunk.text
    header = re.match(r"(?i)^\s*(?:SECTION\s+\d+\b[^\n]*|\d+\.?\s+[A-Za-z][^\n]*)\n", body)
    origin = header.end() if header else 0
    # Remove only known field labels, retaining each quoted source slice.
    marker = re.compile(r"(?im)^(?:[ \t]*\d+\.\d+[ \t]+)?(?:conditions for safe storage|incompatible materials|hazard classification|hazard statement|signal word|conditions to avoid):[ \t]*")
    bounds = list(marker.finditer(body, origin))
    spans = [(origin, bounds[0].start() if bounds else len(body))]
    spans.extend((m.end(), bounds[i+1].start() if i+1 < len(bounds) else len(body)) for i,m in enumerate(bounds))
    for left, right in spans:
        for a,b in sentence_spans(body[left:right]):
            yield chunk.start + left+a, chunk.start + left+b, body[left+a:left+b]


def propose(chunk, products, owner):
    section = chunk.section.split(".")[0]
    for start, end, quote in passages(chunk):
        pairs = []
        if MISSING.search(quote):
            pairs.append(("no_data", section))
        elif section == "8":
            pairs.append(("ppe", quote))
        elif section in {"4", "5", "6"}:
            pairs.append(("emergency", section))
        elif section == "2":
            pairs.extend(("h_code", h) for h in re.findall(r"\bH\d{3}[A-Z]*\b", quote))
            prefix = chunk.text[:start - chunk.start].split("\n")[-1]
            labelled = re.fullmatch(r"\s*hazard classification:\s*", prefix, re.I)
            if labelled or any(mentions(word, quote) for spec in mapping()["classes"].values()
                               for word in spec["classes"] + spec.get("required_identity", [])):
                pairs.append(("hazard_class", quote))
        elif section == "7" or chunk.section == "10.5":
            if (RESTRICT.search(quote) or chunk.section == "10.5") and not NEGATIVE.search(quote):
                for product in products:
                    aliases = [product.name, product.code, *product.synonyms, *product.cas_numbers]
                    if product.id != owner and any(mentions(alias, quote) for alias in aliases):
                        pairs.append(("restriction_name", str(product.id)))
                for key, spec in mapping()["classes"].items():
                    if any(mentions(word, quote) for word in spec["restrictions"]):
                        pairs.append(("restriction_class", key))
        for kind, value in set(pairs):
            yield Fact(version_id=chunk.version_id, chunk_id=chunk.id, type=kind,
                       value=value, start=start, end=end, quote=quote)


def extract(db, version):
    products = db.exec(select(Product)).all()
    seen = {(f.chunk_id, f.start, f.end, f.type, f.value) for f in facts(db, version.id)}
    for c in db.exec(select(Chunk).where(Chunk.version_id == version.id, Chunk.method == "section").order_by(Chunk.id)):
        if not c.matches_source(version.document_text):
            continue
        for f in propose(c, products, version.product_id):
            key = (f.chunk_id, f.start, f.end, f.type, f.value)
            if key not in seen:
                db.add(f)
                seen.add(key)
    db.flush()


def facts(db, version_id):
    return db.exec(select(Fact).where(Fact.version_id == version_id).order_by(Fact.id)).all()


def valid(f, c, version):
    return (version.status == "active" and c is not None and c.version_id == f.version_id == version.id
            and c.matches_source(version.document_text) and f.start is not None and f.end is not None
            and c.start <= f.start < f.end <= c.end and version.document_text[f.start:f.end] == f.quote)


def confirm(db, version, user):
    if version.status != "active" or not user.strip():
        raise ValueError("An active version and reviewer name are required")
    extract(db, version)
    stamp = now()
    for f in facts(db, version.id):
        if not valid(f, db.get(Chunk, f.chunk_id), version):
            raise ValueError("A proposal failed source validation")
        if not f.confirmed_by and not FORBIDDEN.search(f.quote):
            f.confirmed_by, f.confirmed_at = user.strip(), stamp
    # Explicit, source-backed negative statements can establish R5 coverage.
    notes = []
    for c in db.exec(select(Chunk).where(Chunk.version_id == version.id, Chunk.method == "section")):
        if c.section.split(".")[0] == "7" or c.section == "10.5":
            for start, end, quote in passages(c):
                if c.matches_source(version.document_text) and not FORBIDDEN.search(quote):
                    notes.append(dict(chunk_id=c.id, start=start, end=end, quote=quote))
    version.reviewed_storage = notes
    version.reviewer = user.strip()
    db.add(version)
    db.flush()
