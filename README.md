# Temporal, Evidence-Grounded Knowledge System

Submission for the interface.ai / BankGPT Senior Software Engineer knowledge-system build challenge.

## What it does

Given five point-in-time website snapshots, the engine identifies the newest applicable information (or an older snapshot when the user asks an **as-of** question), extracts answerable facts without mixing product tiers, cites the supporting page, and abstains when a figure is not published.

Core interface: `submission/solution.py` exposes `build()`, returning an object with `ingest(snapshot)`, `answer(question, as_of=None)`, and `conflicts()` exactly as specified by the supplied `harness/contract.py`.

```
Snapshot 1 ──┐
Snapshot 2 ──┤  time-indexed, table-aware SQLite FTS5 indexes
Snapshot 3 ──┼───────────────────────────────────────────────┐
Snapshot 4 ──┤                                               │
Snapshot 5 ──┘                         as-of date → version selector
                                                      │
                                         query intent / entity matching
                                                      │
                                  official rate tables / page retrieval
                                                      │
                                    answer + citation OR abstention

Fact histories ── qualified keys ── changed values ── conflict report
```

### How facts are resolved

- **Temporal isolation:** `as_of` uses the latest complete crawl dated on or before the requested date; queries without a date use the newest ingested crawl.
- **Authority:** Published product/rate sheets outweigh calculators, promotions and older informational articles when they disagree.
- **Tier integrity:** A fact is keyed and selected with its account/product, term, loan amount, model-year cohort, balance bracket, and APR vs. APY type intact. In particular, dollar tiers are not interchangeable.
- **Uncertainty controls:** Missing rate tiers and unrecognized named savings products return a non-confident response; requests for a private member account number abstain. An earlier, now-missing fee may be labeled last documented but is *not* asserted as current. The broad retrieval fallback is heuristic, so not every out-of-scope request can be guaranteed to abstain.
- **Conflict lineage:** Rate changes are compared across dated snapshots for the *same qualified fact*, resolved to the most recent supported figure, and cited. Mirror tables are deduplicated, and current official auto-loan APRs are compared
  to overlapping model-year and term examples from stale calculators.
- **Deterministic / fast:** No LLM calls, paid APIs, network requests, randomization, database servers, or embeddings downloads; retrieval uses in-process SQLite FTS5.

## Local development and evaluation

Requires **Python 3.11** with SQLite compiled with FTS5 (standard on most CPython builds). There are no extra pip dependencies and no API keys.

The source website snapshots are intentionally **not in this repository**. Obtain them from the original, privately supplied participant kit. To run the official evaluation, use the kit's own harness and data:

```bash
# From the original participant-kit root, which contains harness/, eval/ and snapshots_zip/:
python harness/run_eval.py --submission /absolute/path/to/this-repo/submission --eval eval/public_eval.jsonl --out results/submission
```

The kit's runner extracts the snapshot ZIPs locally if necessary. Do not check those extracted files or ZIPs into your public GitHub repository.

This project exposes the **Python contract**, not a web service or HTTP API. The
organizers call `build().ingest(...)`, `build().answer(...)`, and `build().conflicts()`
through their own harness. No application server, credentials, database service,
Docker image, or API keys are needed. See `docs/ENGINEERING_DECISIONS.md` for the
reasoning and known limitations. The organizers can use their own copy of the data and evaluator with the `submission` folder.

To run offline, synthetic unit tests included in this repository (Python 3.11):

```bash
python -m unittest discover -s tests -v
python -m compileall -q submission tests
```

## Verification results

| Check | Local outcome |
|---|---:|
| Official 40-question practice evaluation | **96.9%** normalized score (independently rerun after fixes) |
| Synthetic five-question scorer self-test supplied with kit | **100%** |
| Error / timeout count (practice) | 0 |
| Detected stale-answer traps (practice) | 0 |
| Paraphrase and repeat consistency | 100% |
| p95 answering latency (practice) | 0.04 seconds |
| All five crawls indexed | ~5–6 seconds on local Python 3.13 |
| Synthetic unit tests | **22 passing** |

These are measurements against the **provided public evaluation**, not a prediction of the private hidden score. The public evaluation does not include a gold conflict list; conflict recall against hidden gold cases is unmeasured. One public certificate-list item has no machine-checkable required tokens; without the optional LLM judge the supplied scorer assigns it 0 irrespective of list completeness. A correctly generated answer therefore does not guarantee a 100% public score.

## Repository safety / submission

Do not upload `snapshots/`, `snapshots_zip/`, ZIP archives, API keys, or environment files. The included `.gitignore` excludes them by default. This repository contains only solution code, a contract definition, explanatory notes, and tests with fictional fixture data.

When satisfied with the result, push it to a **public GitHub repository** and send the repository URL plus the exact commit SHA to `assignments@interface.ai` as requested by the hiring team. See [`submission/NOTES.md`](submission/NOTES.md) for limitations and design trade-offs.