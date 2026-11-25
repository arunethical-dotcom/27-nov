"""Local CPU inference. Downloads occur only during explicit model preparation."""
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen
from shutil import copyfileobj

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ASSETS = {
    "encoder.onnx": ("sentence-transformers/all-MiniLM-L6-v2", "onnx/model.onnx",
                     "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"),
    "reranker.onnx": ("cross-encoder/ms-marco-MiniLM-L-6-v2", "onnx/model.onnx",
                      "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a"),
    "reranker.json": ("cross-encoder/ms-marco-MiniLM-L-6-v2", "tokenizer.json",
                      "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
}
MODEL_DIR = Path(".retrieval/models")


def verify(path):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ASSETS[path.name][2]:
            raise ValueError(f"Unexpected model checksum: {path.name}")


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, (repo, asset, digest) in ASSETS.items():
        path = directory / name
        if path.exists():
            verify(path)
            continue
        with NamedTemporaryFile(dir=directory, delete=False) as tmp:
            pending = Path(tmp.name)
            try:
                with urlopen(f"https://huggingface.co/{repo}/resolve/main/{asset}", timeout=120) as src:
                    copyfileobj(src, tmp)
                tmp.flush()
                with pending.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError(f"Unexpected model checksum: {name}")
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)


class Model:
    def __init__(self, directory, *, rerank=False):
        name = "reranker" if rerank else "encoder"
        path = directory / f"{name}.onnx"
        verify(path)
        tokenizer = (directory / "reranker.json" if rerank else
                     Path(__file__).resolve().parents[1] / "chunking/tokenizer.json")
        if rerank:
            verify(tokenizer)
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.tokenizer.enable_truncation(512 if rerank else 256)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self.rerank = rerank

    def encode(self, inputs):
        batches = []
        for offset in range(0, len(inputs), 16):
            encoded = self.tokenizer.encode_batch(inputs[offset:offset+16])
            feeds = {name: np.asarray([getattr(item, attr) for item in encoded], dtype=np.int64)
                     for name, attr in (("input_ids", "ids"), ("attention_mask", "attention_mask"),
                                        ("token_type_ids", "type_ids"))}
            output = self.session.run(None, feeds)[0]
            if self.rerank:
                batches.append(output[:, 0])
            else:
                mask = feeds["attention_mask"][:, :, None].astype(np.float32)
                pooled = (output * mask).sum(axis=1) / mask.sum(axis=1)
                batches.append(pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12))
        return np.concatenate(batches)
