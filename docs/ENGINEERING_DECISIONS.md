# Engineering decisions and trade-offs

## Scope and contract

`submission/solution.py` exposes a synchronous Python object with three methods exactly matching the supplied contract. No web server exists: the grader supplies `Snapshot` objects to `ingest()`, then invokes `answer()` and `conflicts()`.

## Architecture

For each complete dated snapshot, `ingest()` parses Markdown and builds a separate in-memory SQLite FTS5 index of passages and complete table rows. It stores the raw `Page` objects keyed by URL, plus rate rows that retain heading, column names, row values, URL, and snapshot ID. `answer()` selects the last snapshot at or before `as_of`, identifies supported product categories, routes structured rate questions to specific selectors, and otherwise uses deterministic full-text retrieval plus source-authority ranking. It returns cited evidence or an explicit abstention. No live website requests, model calls, or persisted bank data exist.

The source selection policy prefers official product rate sheets over marketing copy or illustrative calculators, respects loan term and model-year ranges, and does not conflate APR with APY or different deposit balance tiers. Latest *crawl observation* is not proof of an actual rate *effective date*: it is the strongest observation available from the provided source.

## Conflict detection

The engine extracts qualified table-rate and fee observations and detects changes for identical product and tier identities across crawls. It also compares the official auto-loan rate table with stale vehicle-calculator examples where model-year cohorts overlap and the loan term matches. Conflicts include source URLs, snapshot IDs, competing values, the chosen value, and a short precedence explanation. Unrelated terms, tiers, and non-overlapping cohorts must never be treated as conflicts. This coverage is intentionally limited; other same-snapshot inconsistencies, prose-vs-table conflicts, and semantic policy changes may not be extracted.

## Why these choices

- **SQLite FTS5:** Fast, local and deterministic; available in the Python standard library's SQLite distribution; meets tight latency without paid APIs. Trade-off: lexical search and custom extractors have narrower semantic generalization than an LLM/RAG pipeline.
- **Separate snapshot indexes:** Prevents silently mixing a historic answer with newer values. Trade-off: stores redundant copies and uses memory proportional to the entire crawl history.
- **Table-aware extraction:** Keeps the row's product/term/tier/metric together. Trade-off: assumes recognizable Markdown table structure and needs new parsers for other layouts.
- **Heuristic authority and explicit refusal:** Conservative for sensitive bank facts and known product selectors. Trade-off: the extractive fallback can still return a relevant-sounding passage that does not fully answer an unsupported question.
- **No LLM/API dependencies:** Reproducible, inexpensive, no secrets. Trade-off: limited language understanding and complex multi-hop inference.

## Failure behavior and limitations

The engine abstains when the requested snapshot predates its first observation, when a specific structured rate/tier is not present, when a named savings product cannot be verified, or for private member account identifiers. Invalid `as_of` dates abstain. The provided snapshot contract is assumed to contain valid Markdown strings and monotonic snapshot delivery, as stated by the assignment. Unsupported Markdown layouts, deleted products, internal contradictions beyond the instrumented extractors, vague prose questions, and effective-date disputes remain known risks.

## Production evolution (not implemented)

A production system could add a typed claim graph, canonical product identifiers, explicit valid-from/valid-to date extraction, cross-source conflict edges, wider provenance spans, persistent FTS/SQL storage, versioned embedding search, review queues for low-confidence claims, retrieval benchmarks, and monitoring for both false answers and false abstentions. These are proposals, not implemented features.

## Reproducibility

Run the supplied grader from the private participant-kit root against the published `submission` directory. The public repo deliberately does not contain the snapshot data or the participant evaluation set. The synthetic unit tests run independently via `python -m unittest discover -s tests -v`. The actual grading runtime specified by the kit is Python 3.11; the local validation recorded in the review was Python 3.13, so a separate clean 3.11 environment should be used before formal submission where possible.