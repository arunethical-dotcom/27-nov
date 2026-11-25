"""Evaluate evidence retrieval using independently labelled fixture passages."""
import argparse
from pathlib import Path
from statistics import mean

import yaml
from sqlmodel import Session, create_engine

from app.retrieval.index import DB, INDEX, active_chunks, load_index
from app.retrieval.models import MODEL_DIR, Model
from app.retrieval.search import Search


def metrics(ranking, labels, rows):
    first = []
    for section, quote in labels:
        rank = next((n for n, key in enumerate(ranking, 1)
                     if rows[key].section == section and quote in rows[key].text), None)
        if rank is not None:
            first.append(rank)
    return sum(rank <= 5 for rank in first) / len(labels), 1 / min(first) if first else 0


def evaluate(search, gold):
    for method in ("section", "window", "sentence"):
        for mode in ("bm25", "vector", "hybrid", "reranked"):
            recalls, reciprocals = [], []
            for pair in gold["pairs"]:
                rankings = search.pair(*pair["products"], method, mode)
                evidence = [gold["passages"][label] for label in pair["evidence"]]
                for code, ranking in rankings.items():
                    labels = [(sec, quote) for product, sec, quote in evidence if product == code]
                    if labels:
                        recall, reciprocal = metrics(ranking, labels, search.rows)
                        recalls.append(recall)
                        reciprocals.append(reciprocal)
            yield method, mode, mean(recalls), mean(reciprocals), len(recalls)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=["retrieval"])
    parser.parse_args()
    gold = yaml.safe_load(Path(__file__).with_name("retrieval_gold.yaml").read_text())
    with Session(create_engine(DB)) as db:
        search = Search(db, Model(MODEL_DIR), load_index(INDEX, active_chunks(db)),
                        Model(MODEL_DIR, rerank=True))
        print("| Chunk method | Retriever | Recall@5 | MRR | Labelled directions |")
        print("|---|---|---:|---:|---:|")
        for method, mode, recall, reciprocal, count in evaluate(search, gold):
            print(f"| {method} | {mode} | {recall:.3f} | {reciprocal:.3f} | {count} |")
