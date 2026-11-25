# SDS Chemical Compatibility Advisor
CAT1-037 is a local SDS review application. The current milestone includes ingestion, retrieval, fact confirmation and deterministic advisory checks.

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

## Phase 2 · Retrieve evidence locally

From the repository root in the activated environment, install the pinned requirements,
then load the demo PDFs and prepare the two CPU ONNX models once:

```sh
python -m app.ingest.load fixtures/
python -m app.retrieval.index --prepare-models
python -m eval.run retrieval
```

The preparation step needs network access to Hugging Face and about 175 MiB of local
storage. Model files are checksum-verified. For an offline machine copy the prepared
`.retrieval/models/` directory, then run `python -m app.retrieval.index`. Rebuild
the index after changing active versions or chunks. Both inference sessions use
ONNX Runtime's CPU provider. The structured NumPy file stores ID, fingerprint and
384-dimensional normalized embedding together and is replaced atomically.

The programmatic entry point is `app.retrieval.search.Search`. Use `pair(a, b,
method, mode)` for both storage directions (keys are the product codes of the
searched sheets); use `rank(code, queries, method, mode, purpose)` for one sheet.
Rankings contain chunk IDs. Resolve each through `search.rows` for its version,
page range, section, offsets and original text. The reranker scores up to 20
hybrid candidates per direction. Retrieval returns evidence only.

The gold file covers all 15 unordered demo pairs. Each labelled direction is a
separate query against one sheet. Recall@5 is the fraction of distinct labelled
passages in its top five; reciprocal rank is the inverse rank of its first
relevant chunk (zero on a miss). Both metrics are averaged over 26 labelled
directions. Unlabelled reverse directions still run. Results characterize these
fictional fixtures only.

## Phase 3 · Review and advise

The retrieval scores above were measured on six one-page fictional sheets, with
roughly ten in-scope chunks per sheet. They demonstrate that the pipeline runs.
Real-world retrieval accuracy requires independent labelled SDSs.

For the fictional demo, run:
```sh
python -m app.ingest.load fixtures/ --confirm-demo
python -m app.advise ASTER-100 BOREAL-200 CIRRUS-300
python -m app.advise DAHLIA-400 EMBER-500
```
For manual review, omit the flag, open the home page, select the active version,
inspect its proposals and storage passages, and submit a reviewer name. Clients
can POST {"user":"Reviewer"} to /versions/{id}/facts/confirm. Confirmation applies
to the active version. The demo flag is intended only for fictional inputs.
Extraction uses regexes. Each fact records its chunk, document offsets and verbatim
quote. Active source validation also checks the enclosing chunk. Missing PPE or
emergency instructions are represented by their cited no-data sentence when
available, otherwise an empty evidence list. Product IDs, active SDS version IDs
and chunk IDs are database keys. The session UUID identifies one advisory call.

All confirmed facts in Sections 2, 7, 10 and 14 participate before ranking.
BM25 orders optional evidence and chemical-card passages without a model download.
R1 validates a named restriction. R2 requires both supporting citations. R3
defaults off in rules/class_map.yaml. R4 reports explicit no-data or verification
gaps. R5 is conservative: it requires confirmed class evidence and fully interpreted storage restrictions or explicit clear
statements in both Section 7 and 10.5 of each sheet. Section titles alone
cannot establish completeness. H290 and “corrosive to metals” describe metal
corrosivity; the strong-acid mapping also requires explicit identity evidence.
All examples are decision support only. Confirm with a qualified person.

Full CLI examples from the demo database: eval/examples/step_1.json and
eval/examples/dahlia_ember.json. Later imports can assign different database IDs.
