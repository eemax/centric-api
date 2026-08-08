# Fetch performance: future work

Ideas for making full fetches significantly faster. Nothing here is implemented; this documents
measured evidence and design considerations for when fetch wall-clock time becomes worth spending
effort on. Delta fetches are already fast; everything below is about full fetches.

## Where the time goes

A full fetch is dominated by request count, not data volume or local processing. Measured from
real run logs (2026-07, ~1.2M records total):

| Endpoint | Records | Page limit | Requests | Duration | Per request |
| --- | --- | --- | --- | --- | --- |
| bom_lines | 208,524 | 50 | 4,171 | 29.5 min | ~0.42 s |
| size_chart_dimensions | 505,825 | 500 | 1,012 | 14.3 min | ~0.85 s |
| colorways | 66,191 | 50 | 1,324 | 21.9 min | ~1.0 s |
| document_revisions | 90,049 | 50 | 1,801 | 17.1 min | ~0.57 s |
| styles | 28,393 | 50 | 568 | 27.5 min | ~2.9 s |

Note the first two rows: at `limit: 500` one request returns 10x the records for roughly 2x the
time. Most of a small-page request is round-trip and per-request server overhead. The whole run is
also strictly sequential — one request in flight, pages sequential within an endpoint, endpoints
sequential within the run — so these durations add up directly to a multi-hour full fetch.

Local processing is not the problem. On the same dataset, ingest record lookups measure ~5 us
each, a hash-only scan of all 1.2M cached records takes ~3 s, and JSON parsing is seconds per
endpoint. Optimizing anything local before the fetch path is optimizing the wrong 1%.

## Suggestion 1: raise per-endpoint page limits (config only)

Set `limit: 500` in `config/fetcher.yml` for the high-volume endpoints (`bom_lines`, `colorways`,
`document_revisions`, `documents`, `supplier_quotes`, `supplier_quote_masters`,
`product_sources`, `size_chart_revisions`, ...). Expected effect: ~10x fewer requests for those
endpoints, plausibly cutting a full fetch 3-5x with no code changes.

Considerations:

- Per-request time grows with payload size, not just row count. `styles` already takes ~2.9 s per
  50-record page, so its server-side cost is payload-bound; a 500-record styles page may be slow
  or hit response-size or server-timeout limits. Tune per endpoint from measured runs rather than
  applying one blanket limit.
- Larger pages raise the retry cost of a failed request (one flaky 500-record page refetches 500
  records). With checkpointed skip-based resume this stays bounded; it is a tradeoff, not a
  blocker.
- Verify the server honors the requested limit at higher values instead of silently clamping,
  since count validation math divides by the requested limit.

## Suggestion 2: bounded request concurrency (code change)

The fetcher currently holds exactly one request in flight for the entire run. Two designs, from
simpler to more invasive:

**Concurrent endpoints.** Fetch 2-3 endpoints at a time, each internally sequential. Per-endpoint
checkpoint, resume, output-file, and count-validation semantics stay untouched; the change is
confined to the loop in `commands/fetch.py` plus making per-endpoint logging interleave cleanly.
Expected effect roughly matches the concurrency level while the long-tail endpoint (usually
`bom_lines`) still bounds the run.

**Concurrent pages within an endpoint.** Skip-based pagination with a known `expected_count`
makes page fetching embarrassingly parallel: issue pages `skip=0,N,2N,...` with a small worker
pool and write results in page order. This attacks the long-tail endpoint directly, so it
compounds with suggestion 1 (a full fetch could drop from hours to ~10-20 minutes), but it cuts
deeper into current invariants:

- The checkpoint format assumes a single frontier (`next_skip`); parallel pages need either
  completed-page tracking or a "lowest contiguous page" frontier to keep resume correct.
- Output files are append-ordered today; either buffer out-of-order pages before appending or
  write per-page temp files and concatenate on completion.
- Delta-window consistency: pages are snapshots of a moving collection; parallel fetching does
  not make this worse than sequential fetching, but the existing count-validation warnings should
  stay authoritative.

Considerations for either design:

- Respect the server. Make concurrency a config knob (default 1 to preserve current behavior),
  watch for 429/5xx rates during rollout, and keep the existing backoff-retry per request.
- The auth session and `httpx.Client` are shared; confirm token refresh is safe under concurrent
  requests before enabling either mode.
- `fetch.lock` already serializes whole runs, so concurrency inside one run does not create
  cross-run races.

## Suggested order

1. Raise limits for the endpoints in the table above, one at a time, watching per-request
   durations in `logs/fetch.log`.
2. If full fetches still hurt, add concurrent endpoints (small, low-risk).
3. Only then consider concurrent pages, starting with the one or two endpoints that dominate the
   run.
