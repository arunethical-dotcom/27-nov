# Known limits

- A sentence chunk spanning a page break can omit the subsection label before the instruction, such as “7.2 Conditions for safe storage:”. The corresponding section chunk retains the complete passage.
- Retrieval scores in the README were measured on six one-page fictional sheets with about ten in-scope chunks each. They show the pipeline runs; real-world accuracy needs independently labelled real SDSs.
- Fact extraction is rule-based. Restrictions phrased in unusual ways, tables, scanned PDFs and non-English SDSs are not handled. Every fact needs human confirmation before rules use it.
- Product identity for a new upload uses product code, then exact product name, then a shared CAS number. A supplier that changes all three will be treated as a new product.
- EVIDENCE_CHANGED compares the cited quotes for CONFLICT and NO_CONFLICT_FOUND pairs, and the list of missing items for UNRESOLVED pairs. A reworded sentence that keeps the same meaning is still flagged; this is deliberate (a human reviews it).
- "Keep current" deletes the uploaded pending file. "Decide later" leaves it pending; while it is pending, it is never used by any check.
- Concentration-dependent rules, separation distances, reactive-group tables (R3 is off by default) and regulatory compliance claims are out of scope.
