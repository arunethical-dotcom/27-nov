# SDS Chemical Compatibility Advisor
CAT1-037 is a local SDS review application. The current milestone implements PDF ingestion and chunking.

## Run locally
The dependencies in requirements.txt were frozen from a clean Python 3.11.18 virtual environment. CI uses Python 3.11 on Ubuntu.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m app.ingest.load fixtures/
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000. The loader writes advisor.db in the current directory, skips files already present by SHA-256 and stages replacement sheets for confirmation.

## Chunk offsets and windows
Page and chunk metadata use one-based page numbers and zero-based, half-open character offsets into the stored SDS document text. page_start and page_end are inclusive; page is the starting page. Section 0 holds the header. Removed page furniture separates contiguous spans. Each retained chunk can be checked directly against its source slice.

Windows use the bundled sentence-transformers/all-MiniLM-L6-v2 tokenizer and require no model download at runtime. The defaults in app/chunking are 200 content tokens with 40 tokens of overlap, excluding special tokens. Callers can lower the window size and configure the overlap.

The upstream tokenizer configuration is distributed under Apache-2.0. Source: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/main/tokenizer.json. SHA-256: be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037.

Decision support only. Confirm with a qualified person.
