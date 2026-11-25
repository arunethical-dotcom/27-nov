# SDS Chemical Compatibility Advisor
CAT1-037 is a local SDS review application: ingestion, hybrid retrieval, fact confirmation, deterministic cited storage verdicts, PPE and emergency cards, sessions, and revision-aware rechecks. **For the 5-minute demo, follow [DEMO.md](DEMO.md).**

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
quote. Active source validation also checks the enclosing chunk. Missing PPE or emergency instructions create verification items. Explicit no-data passages are cited in those items. Product codes and names identify chemicals; version and chunk IDs identify their sources. The session UUID identifies one advisory call.

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

## Phase 4 · Run the session demo

From the repository root with the dependencies installed and the virtual environment active, start with a clean working directory for the database. Run the third command in another terminal.

```sh
python -m app.ingest.load fixtures/ --confirm-demo
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
xdg-open http://127.0.0.1:8000/sessions
```

Choose **Open fixture demo** for the six-product matrix. Select either conflict to see its cited SDS passages, or select a product name to inspect PPE, emergency and storage evidence. **Create session** starts an empty session for adding products individually. Local HTMX assets support offline use, and forms also work with ordinary browser navigation.

POST /sessions accepts a JSON name. POST /sessions/{id}/products accepts a JSON code. The second endpoint returns the session advisory and treats repeat additions as idempotent. Each addition commits membership and its new pairs together. Existing results retain their version IDs. GET /sessions/{id}/download exports JSON. The session read path verifies saved evidence against active, confirmed sources. Pair results pinned to an archived version are kept as history and are not shown in the current matrix.

Pairs carry product codes, names, version IDs and individual abstention flags. Session abstention requires at least one pair and every pair abstaining. Card verification items distinguish an explicit no-data passage from missing instructions and retain citations for explicit gaps.

## Phase 5 · Revision-aware rechecks

A sheet for a product that already exists never replaces the active one by itself.

1. **Stage.** `POST /upload` (or the loader) matches the product by code, then name, then CAS number. A known product's new file is stored as a **pending** version. An identical file is rejected as a duplicate. A new product becomes active and goes to fact review.
2. **Preview.** `GET /versions/{id}/preview` runs the real replacement inside a database transaction and rolls it back, so the rules and citation checks are exactly the ones a confirmed replacement uses, and nothing is saved. It shows word-level changes in Sections 2, 7, 8, 10.5 and 14 and every saved pair that includes the product, with old and new verdicts.
3. **Decide.** `POST /versions/{id}/replace` with a reviewer name archives the old version, activates the new one, confirms its facts under that name, re-checks every saved pair, moves session membership to the new version and writes a change report. `POST /versions/{id}/discard` keeps the current version and deletes the pending file.
4. **Flags.** `STATUS_CHANGED` means the verdict changed. `EVIDENCE_CHANGED` means the verdict is the same but what it rests on changed: the cited quotes for a decision, or the missing items for an unresolved pair. Both appear in the session matrix, the pair page, the JSON (`change_flag`, `previous_verdict`, `change_report_id`) and `GET /change-reports/{id}`.

To demo the upload live, load everything except the revision:

```sh
python -m app.ingest.load fixtures/ --confirm-demo --exclude '*rev2*'
```

The fixture contract `fixtures/expected.yaml` step 3 is checked by `tests/test_revisions.py`: Aster + Cirrus becomes CONFLICT (status changed), Aster + Boreal stays CONFLICT with changed evidence, and the other Aster pairs are unchanged.
