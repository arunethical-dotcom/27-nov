# Demo script · CAT1-037 SDS Chemical Compatibility Advisor

About 5 minutes. Every file used is a **fictional** demo sheet marked "FICTIONAL DEMO ONLY". Never use them for real chemical handling.

## Before you present (one time, about 2 minutes)

On Fedora, from the repository root:

```sh
python3.11 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
rm -f advisor.db                                   # start clean
python -m app.ingest.load fixtures/ --confirm-demo --exclude '*rev2*'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/sessions in the browser. Keep `fixtures/SDS-Aster-100-rev2.pdf` ready to upload in Step 4.

## Step 1 · The problem (30 s)

"Storage decisions need data from several safety data sheets. Staff rarely cross-check them, and when a supplier sends a new SDS version, nobody re-checks the decisions that relied on the old one."

## Step 2 · Six products, fifteen pairs (1 min)

Click **Open fixture demo**. Point at:
- **2 conflicts**: Aster 100 + Boreal 200 (R1, Aster names Boreal) and Dahlia 400 + Ember 500 (R2, class match).
- Everything else is **UNRESOLVED**, and the legend says why: missing SDS data is never shown as safe.

## Step 3 · Two-sided evidence (1 min)

Click the **Dahlia 400 + Ember 500** conflict. Show both quotes side by side:
- Dahlia, Section 7: "Keep away from strong oxidising agents."
- Ember, Section 2: "H272 May intensify fire; oxidiser."

"A class rule is only shown as a conflict when **both** sentences are found and validated against the stored SDS text."

Optionally click a product name to show its chemical card: PPE (Section 8), emergency steps (Sections 4, 5, 6) and the "What you need to verify" list.

## Step 4 · The headline: a new SDS version arrives (2 min)

Click **Upload SDS**, choose `SDS-Aster-100-rev2.pdf`, enter your name, then **Upload**.

The app does **not** replace anything. It asks:
> Replace Aster 100 version 1 (2026-11-01) with version 2 (2026-11-20)?

Show:
1. **What changed in the sheet**: Sections 7 and 10.5, with " or Cirrus 300 (CIRRUS-300)" highlighted as new.
2. **Saved pairs this would affect**: 1 status changed · 1 evidence changed · 3 no change.
   - Aster + Cirrus: UNRESOLVED → **CONFLICT** (status changed).
   - Aster + Boreal: CONFLICT → CONFLICT, but **the evidence changed — review required**. "Most tools would show nothing here, because the colour did not change."

Enter your name and click **Replace with version 2**.

## Step 5 · Proof and audit trail (30 s)

The change report shows who confirmed it and when, and version 1 is archived, not deleted. Go back to the session: the matrix now shows "status changed" and "evidence changed" tags on the two Aster cells. Click **Download JSON** to show the M1 advisory output.

## Closing line

"When an SDS changes, we tell you exactly what changed, whether any storage decision is affected, and what you still need to check."

## Honest limits to mention if asked

- Retrieval scores are on six one-page fictional sheets; they show the pipeline works, not real-world accuracy.
- Extraction is rule-based (regex) and needs human confirmation; scanned or multilingual SDSs are out of scope.
- See KNOWN_LIMITS.md.
