"""Build one validated advisory from an active-version read snapshot."""
import argparse
import re
from itertools import combinations
from types import SimpleNamespace
from uuid import uuid4

from sqlmodel import Session, create_engine, select
from sqlalchemy import text
from app.db import Chunk, SDSVersion
from app.facts import CLEAR, FORBIDDEN, RESTRICT, facts, mapping, mentions, valid
from app.retrieval.search import Search
from app.schemas import Advisory, Chemical, Evidence, Gap, Pair, Passage, ProductRef


class Sheet:
    def __init__(self, db, product, version):
        self.product, self.version = product, version
        self.ref = ProductRef(code=product.code, name=product.name)
        self.chunks = {r.id: r for r in db.exec(select(Chunk).where(Chunk.version_id == version.id))}
        self.confirmed = [f for f in facts(db, version.id) if f.confirmed_by and f.confirmed_at]
        self.notes = [SimpleNamespace(**n, version_id=version.id) for n in version.reviewed_storage or []
                      if version.reviewer]
        self.rule_facts = [f for f in self.confirmed if self.section(f).split(".")[0] in {"2","7","10","14"}]

    def section(self, fact):
        chunk = self.chunks.get(fact.chunk_id)
        return chunk.section if chunk else ""

    def passage(self, fact):
        c = self.chunks.get(fact.chunk_id)
        if not valid(fact, c, self.version) or FORBIDDEN.search(fact.quote):
            return None
        return Passage(chunk_id=c.id, section=c.section, page=c.page, quote=fact.quote)

    def card(self, search):
        gaps = []
        def take(section, purpose):
            candidates = [f for f in self.confirmed if self.section(f).split(".")[0] == section
                          and f.type in {"ppe", "emergency", "no_data"}]
            ranking = search.rank(self.product.code, [purpose, "No data available"], "section", "bm25", purpose)
            order = {key:i for i,key in enumerate(ranking)}
            candidates.sort(key=lambda f: order.get(f.chunk_id, len(order)))
            instructions, missing = [], []
            for f in candidates:
                p = self.passage(f)
                if p:
                    (missing if f.type == "no_data" else instructions).append(p)
            if missing or not instructions:
                gaps.append(Gap(product=self.ref, section=section,
                                reason="SDS_SAYS_NO_DATA" if missing else "NOT_LOCATED",
                                evidence=missing))
            return instructions
        return Chemical(product=self.ref, sds_version=self.version.id,
                        ppe=take("8", "ppe"), emergency={s: take(s, "emergency") for s in ("4","5","6")},
                        to_verify=gaps, storage_notes=[p for f in self.notes if (p := self.passage(f))])


def matches(f, spec):
    return ((f.type == "h_code" and f.value in spec["codes"]) or
            (f.type == "hazard_class" and any(mentions(w, f.value) for w in spec["classes"])))


def storage_covered(sheet, note, facts_by_section, search):
    """Require a confirmed interpretation of every term in a restriction list."""
    if CLEAR.fullmatch(note.quote):
        return True
    same = [f for f in facts_by_section if f.chunk_id == note.chunk_id and
            (f.start, f.end) == (note.start, note.end) and sheet.passage(f)]
    terms = []
    for f in same:
        if f.type == "restriction_name":
            target = next((p for p in search.products.values() if str(p.id) == f.value), None)
            if target:
                terms.extend([target.code, target.name, *target.synonyms, *target.cas_numbers])
        if f.type == "restriction_class" and f.value in mapping()["classes"]:
            terms.extend(mapping()["classes"][f.value]["restrictions"])
    if not terms:
        return False
    remainder = RESTRICT.sub("", note.quote, count=1)
    remainder = re.sub(r"^\s*(?:together\s+)?with\s+", "", remainder, flags=re.I)
    for term in sorted(set(terms), key=len, reverse=True):
        remainder = re.sub(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", "", remainder, flags=re.I)
    return not re.sub(r"\b(?:and|or)\b|[\s,.;()]", "", remainder, flags=re.I)


def decide(a, b, search, enable_r3=False):
    sheets = [a, b]
    ids = dict(a=a.ref, b=b.ref, a_version_id=a.version.id, b_version_id=b.version.id)
    ranking = search.pair(a.product.code, b.product.code, "section", "bm25")
    def order(side, f):
        seq = ranking[sheets[side].product.code]
        return seq.index(f.chunk_id) if f.chunk_id in seq else len(seq)

    def evidence(side, f):
        p = sheets[side].passage(f)
        return Evidence(**p.model_dump(), side="AB"[side]) if p else None

    def gap(side, section, reason="NOT_LOCATED"):
        return Gap(product=sheets[side].ref, section=section, reason=reason)

    def result(rule, verdict, proof, gaps):
        return Pair(**ids, abstained=verdict == "UNRESOLVED", rule_id=rule, verdict=verdict, evidence=proof, to_verify=gaps)

    # Rankings only order alternative proofs. Every eligible fact is inspected.
    candidates = {"R1": [], "R2": [], "R3": []}
    classes = mapping()["classes"]
    for i, sheet in enumerate(sheets):
        other = sheets[1-i]
        for f in sheet.rule_facts:
            if f.type == "restriction_name" and f.value == str(other.product.id):
                candidates["R1"].append([(i,f)])
            if f.type == "restriction_class" and f.value in classes:
                spec = classes[f.value]
                identities = [h for h in other.rule_facts if h.type == "hazard_class"
                              and any(mentions(w,h.value) for w in spec.get("required_identity", []))]
                for h in other.rule_facts:
                    if matches(h,spec):
                        proof = [(i,f),(1-i,h)]
                        if spec.get("required_identity"):
                            candidates["R2"].extend(proof + [(1-i,x)] for x in identities)
                        else:
                            candidates["R2"].append(proof)
        if enable_r3:
            first, second = mapping()["reactive_groups"]["groups"]
            for x in sheet.rule_facts:
                for y in other.rule_facts:
                    if matches(x,classes[first]) and matches(y,classes[second]):
                        candidates["R3"].append([(i,x),(1-i,y)])
    for rule, proofs in candidates.items():
        if not proofs:
            continue
        # Prefer explicit hazard codes, then use retrieval rank to break ties.
        proofs.sort(key=lambda proof: (not any(f.type == "h_code" for _,f in proof),
                                       not any(f.type.startswith("restriction_") and sheets[i].section(f).split(".")[0] == "7" for i,f in proof),
                                       sum(order(i,f) for i,f in proof)))
        if rule == "R1":
            all_quotes = []
            for [(side, fact)] in proofs:
                passage = evidence(side, fact)
                if passage and passage not in all_quotes:
                    all_quotes.append(passage)
            if all_quotes:
                return result(rule, "CONFLICT", all_quotes, [])
        missing = []
        for proof in proofs:
            quotes = [evidence(i,f) for i,f in proof]
            if all(quotes):
                return result(rule, "CONFLICT", quotes, [])
            for (i,f),quote in zip(proof,quotes):
                item = gap(i, sheets[i].section(f))
                if quote is None and item not in missing:
                    missing.append(item)
        return result("R4", "UNRESOLVED", [], missing)

    gaps, quotes = [], []
    for i,sheet in enumerate(sheets):
        for sec in ("2","7","10.5"):
            def within(f):
                label = sheet.section(f)
                return label == sec or label.startswith(sec + ".")
            fs = [f for f in sheet.rule_facts if within(f)]
            unknown = [f for f in fs if f.type == "no_data" and evidence(i,f)]
            if unknown:
                gaps.append(gap(i,sec,"SDS_SAYS_NO_DATA"))
                quotes.append(evidence(i,unknown[0]))
                continue
            if sec == "2":
                covered = [f for f in fs if f.type in {"hazard_class","h_code"} and evidence(i,f)]
            else:
                notes = [n for n in sheet.notes if within(n)]
                covered = [n for n in notes if evidence(i,n) and storage_covered(sheet,n,fs,search)]
                # Mixed, unparsed, or unverified storage statements require review.
                if len(covered) != len(notes):
                    covered = []
            if covered:
                quotes.append(evidence(i,covered[0]))
            else:
                gaps.append(gap(i,sec))
    return result("R4" if gaps else "R5", "UNRESOLVED" if gaps else "NO_CONFLICT_FOUND", quotes, gaps)


def advise(db, codes, enable_r3=None):
    codes = list(dict.fromkeys(codes))
    if not codes:
        raise ValueError("Select at least one product")
    search = Search(db, None, {})
    sheets = []
    for code in codes:
        if code not in search.products:
            raise ValueError(f"No active SDS for {code}")
        sheets.append(Sheet(db, search.products[code], db.get(SDSVersion, search.versions[code])))
    flag = mapping()["reactive_groups"]["enabled"] if enable_r3 is None else enable_r3
    pairs = [decide(a,b,search,flag) for a,b in combinations(sheets,2)]
    cards = [s.card(search) for s in sheets]
    return Advisory(session_id=str(uuid4()), chemicals=cards, pairs=pairs,
                    abstained=bool(pairs) and all(p.abstained for p in pairs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+")
    args = parser.parse_args()
    with Session(create_engine("sqlite:///advisor.db")) as db:
        db.execute(text("BEGIN"))
        try:
            print(advise(db, args.codes).model_dump_json(indent=2))
        except ValueError as exc:
            parser.error(str(exc))
