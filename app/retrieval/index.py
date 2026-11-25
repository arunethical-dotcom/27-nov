"""Build an atomic, content-checked NumPy index of the active SDS versions."""
import argparse
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from sqlmodel import Session, create_engine, select

from app.db import Chunk, SDSVersion
from app.retrieval.models import MODEL_DIR, Model, prepare, ASSETS

DB = "sqlite:///advisor.db"
INDEX = Path(".retrieval/chunks.npy")
DTYPE = [("chunk_id", "<i8"), ("fingerprint", "S64"), ("embedding", "<f4", (384,))]


def active_chunks(db):
    return db.exec(select(Chunk).join(SDSVersion).where(
        SDSVersion.status == "active").order_by(Chunk.id)).all()


def fingerprint(row):
    return hashlib.sha256((ASSETS["encoder.onnx"][2] + row.text).encode()).hexdigest().encode()


def build_index(rows, model, path):
    if not rows:
        raise ValueError("No active chunks. Run python -m app.ingest.load fixtures/ first.")
    records = np.empty(len(rows), dtype=DTYPE)
    records["chunk_id"] = [r.id for r in rows]
    records["fingerprint"] = [fingerprint(r) for r in rows]
    texts = list(dict.fromkeys(r.text for r in rows))
    vectors = dict(zip(texts, model.encode(texts), strict=True))
    records["embedding"] = [vectors[r.text] for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        temp = Path(tmp.name)
        try:
            np.save(tmp, records, allow_pickle=False)
            tmp.flush()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def load_index(path, rows):
    records = np.load(path, allow_pickle=False)
    expected = {r.id: fingerprint(r) for r in rows}
    if (records.dtype != np.dtype(DTYPE) or len(records) != len(expected) or
            dict(zip(records["chunk_id"], records["fingerprint"])) != expected):
        raise ValueError("Active chunks changed; rebuild with python -m app.retrieval.index")
    return dict(zip(records["chunk_id"], records["embedding"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-models", action="store_true")
    args = parser.parse_args()
    if args.prepare_models:
        prepare(MODEL_DIR)
    with Session(create_engine(DB)) as db:
        rows = active_chunks(db)
        build_index(rows, Model(MODEL_DIR), INDEX)
        print(f"Indexed {len(rows)} active chunks in {INDEX}")
