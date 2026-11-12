# Temporal knowledge system — architecture and trade-offs

This submission implements `build()` and the exact `ingest`, `answer`, and
`conflicts` interface from `harness/contract.py`. It uses **only the snapshots
passed to `ingest`**. It makes no HTTP requests, requires no API keys and has
no paid runtime dependencies. Python 3.11 and SQLite FTS5 are sufficient.

Each full snapshot is indexed separately in an in-memory SQLite FTS5 search
index. Markdown table headers, table rows and surrounding headings are
retained together, so facts are never extracted from a cell without its term,
tier and account name. Other prose is split into short, cited passages.
`answer(as_of=...)` selects the newest complete snapshot on or before the
requested date (never future information). Unqualified questions select the
latest ingested snapshot. A deterministic retrieval/reranking step prioritizes
current product/rate sheets above older articles, calculator examples and
marketing materials. Structural extractors read loan terms, vehicle model-year
ranges, deposit balance tiers, APY versus dividend rates, and mortgage loan
amount bands. A conservative fallback extracts the most relevant supported
passage, and missing or unpublished facts trigger abstention in known structural paths.
The general-purpose extractive fallback is a heuristic and does not guarantee
abstention in every out-of-scope situation; conservative guards handle named
unknown savings products and member-private account identifiers.

`conflicts()` compares rate-table and other structured fact histories using
keys that contain source identity, product, term, tier and metric. This avoids
falsely flagging different terms, rate versus APY, or different accounts as
conflicting. For a changed fact, the latest observed official rate is selected,
with dated snapshot citations and a reason. Additionally, it compares overlapping vehicle model-year cohorts and terms from the
official auto-loan table against conflicting stale calculator rows within the
latest crawl. This explicitly covers one cross-page contradiction class without
conflating unrelated products. All outputs are deterministic, so repeat calls
return the same result.

Limitations: The fallback is extractive rather than an LLM and may not handle
complex multi-hop prose or all possible paraphrases. Historical snapshots are
point-in-time observations, not guaranteed effective-time records; this system
intentionally will not infer values before the first snapshot. The documented source precedence is heuristic rather than
a machine-verifiable effective-time guarantee. No complete cross-page
conflict-extraction claim is made. There is no
runtime hardcoding of the expected public evaluation answers. Source citations
refer to the URLs provided by the snapshots; the supplied snapshots are not
included with the submission.

## Run

From the **original participant-kit root** (with `harness/`, `eval/` and
`snapshots_zip/` present):

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl --out results/submission
```

The grader can also pass snapshots already loaded via `ingest()`; there is no
path or file dependency on the participant-kit dataset.