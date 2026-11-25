"""Retrieve SDS passages in both directions. No verdict logic."""
from collections import defaultdict
import re

import numpy as np
from rank_bm25 import BM25Okapi
from sqlmodel import select

from app.db import Product, SDSVersion
from app.retrieval.index import active_chunks

TOKEN = re.compile(r"\bUN[ -]?\d{4}\b|\w+(?:[-‐‑]\w+)*", re.I)
SCOPES = {"storage": {"7", "10", "2", "9", "14"}, "ppe": {"8"}, "emergency": {"4", "5", "6"}}


def tokens(text):
    return [re.sub(r"^un[ -](?=\d{4}$)", "un", t.lower()) for t in TOKEN.findall(text)]


def rrf(rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(ranking, 1):
            scores[key] += 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


class Search:
    """A read-session snapshot. Open a fresh instance after rebuilding the index."""
    def __init__(self, db, encoder, vectors, reranker=None):
        self.db, self.encoder, self.vectors, self.reranker = db, encoder, vectors, reranker
        active = db.exec(select(Product, SDSVersion).join(SDSVersion).where(SDSVersion.status == "active")).all()
        self.products = {p.code: p for p, _ in active}
        self.versions = {p.code: v.id for p, v in active}
        self.rows = {r.id: r for r in active_chunks(db)}
        self.query_vectors = {}
        self.logits = {}

    def check_active(self, code):
        current = self.db.exec(select(SDSVersion.id).where(
            SDSVersion.product_id == self.products[code].id, SDSVersion.status == "active")).first()
        if current != self.versions[code]:
            raise ValueError("Active SDS changed; reopen retrieval and rebuild its index")

    def queries(self, code):
        self.check_active(code)
        product = self.products[code]
        queries = [f"do not store with {product.name}", f"incompatible with {product.name}",
                   product.name, code, *product.synonyms, *product.cas_numbers]
        # These literal strings provide query expansion only.
        for row in self.rows.values():
            if row.version_id == self.versions[code] and row.method == "section" and row.section.split(".")[0] == "2":
                queries.extend(re.findall(r"\bH\d{3}[A-Z]*\b", row.text, re.I))
                for label in re.findall(r"^Hazard classification:\s*([^\n]+)", row.text, re.I | re.M):
                    if not re.search(r"not assessed|unknown|no data|undisclosed", label, re.I):
                        queries.append(label.strip())
        return list(dict.fromkeys(q for q in queries if q))

    def rank(self, code, queries, method, mode, purpose="storage"):
        self.check_active(code)
        scope = SCOPES[purpose]
        rows = [r for r in self.rows.values() if r.version_id == self.versions[code] and
                r.method == method and r.section.split(".")[0] in scope]
        if not rows or not queries:
            return []
        def order(scores):
            return [rows[i].id for i in np.argsort(-scores, kind="stable")]

        if mode in {"bm25", "hybrid", "reranked"}:
            corpus = [tokens(r.text) for r in rows]
            bm = BM25Okapi(corpus)
            # Exclude zero-overlap rows, even when small-corpus IDF is negative.
            lexical = []
            for query in queries:
                terms = tokens(query)
                matched = {r.id for r, words in zip(rows, corpus) if set(terms).intersection(words)}
                lexical.append([i for i in order(bm.get_scores(terms)) if i in matched])
            bm_rank = rrf(lexical)
            if mode == "bm25":
                return bm_rank

        if mode not in {"vector", "hybrid", "reranked"}:
            raise ValueError(f"Unknown retrieval mode: {mode}")
        missing = list(dict.fromkeys(q for q in queries if q not in self.query_vectors))
        if missing:
            self.query_vectors.update(zip(missing, self.encoder.encode(missing), strict=True))
        matrix = np.stack([self.vectors[r.id] for r in rows])
        vec_rank = rrf(order(matrix @ self.query_vectors[q]) for q in queries)
        if mode == "vector":
            return vec_rank
        hybrid = rrf([bm_rank, vec_rank])
        if mode == "hybrid":
            return hybrid
        if self.reranker is None:
            raise ValueError("Cross-encoder required for reranked retrieval")
        query = " ".join(queries)
        candidates = hybrid[:20]
        missing_pairs = list(dict.fromkeys((query, self.rows[i].text) for i in candidates
                                           if (query, self.rows[i].text) not in self.logits))
        if missing_pairs:
            self.logits.update(zip(missing_pairs, self.reranker.encode(missing_pairs), strict=True))
        return sorted(candidates, key=lambda i: -self.logits[query, self.rows[i].text])

    def pair(self, a, b, method, mode):
        if a == b:
            raise ValueError("Two distinct products required")
        return {a: self.rank(a, self.queries(b), method, mode),
                b: self.rank(b, self.queries(a), method, mode)}
