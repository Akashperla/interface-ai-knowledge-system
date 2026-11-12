# Public repository security and data-handling boundaries

This solution requires **no API keys**, private credentials, cloud account, or network connection. It uses the website snapshots only as in-memory data supplied by the evaluator at run time.

Never commit the five original website snapshot ZIPs, extracted `snapshots/` folders, bundled assessment data, private evaluation outputs, `.env` files, credentials, tokens, or runtime logs. The `.gitignore` includes common exclusions; check `git ls-files` and `git log --all` before publication because `.gitignore` cannot hide data already tracked or committed.

A local-only file or snapshot reference may appear in an answer's source citation when run by the authorized evaluator. This is **not** a license to redistribute that underlying document. Reviewers run the submission against their own privately provided snapshots.

The supplied Markdown is processed as untrusted text; no retrieved content is executed or handed to an LLM/tool as instructions. SQLite queries are parameterized and FTS table identifiers are built only from an internally controlled integer. The parser uses bounded passage sizes. This is not an end-user web service and does not implement authentication, authorization, or rate limiting.

If prohibited material has already been published, stop; remove it from history, revoke exposed credentials, and validate remote refs before proceeding. No such cleanup is implied by creating this clean package.