# Evidence freshness and revision history

`runtime.cache.evidence_ttl_hours` in `data_manifest.yaml` sets the maximum reuse age (default 24 hours). No secrets or .env edits are required.

- Fresh assembled pair contexts are reused, preserving their original assembly timestamp.
- Expired contexts are rebuilt. The same request caps shared source-cache TTLs at the configured maximum; shorter source TTLs remain shorter.
- Refresh source knowledge bypasses assembled context reads and shared JSON/text API cache reads, including retrieval workers. It does not delete another request's cache or change local dataset snapshots.
- Refreshed contexts replace the current pair file atomically. Reversed input order reuses an existing readable pair file.
- Changed SQLite source payloads append the superseded snapshot to `cache_history` in the same database transaction as updating the current entry. Identical source payloads do not add history rows. Assembled-context revisions are also recorded in this database when SQLite is enabled.
- Analysis uses the current snapshot, not a union of all historical values. This avoids adding old FAERS counts to new counts or reviving removed records. Revision history is retained for audit, not injected into the model as current evidence.

The frontend's Evidence freshness disclosure reports context assembly time and cache/rebuild/refresh status per pair. This is not a source publication date or a claim that every remote API succeeded. Sources can still be unavailable or return partial data. New publications are not detected immediately during a valid cache window; use refresh when current retrieval is required.

History retention currently follows explicit cache deletion and prefix cleanup. A production retention/size policy should be defined before sustained public deployment. File-only source-cache mode does not preserve SQLite history. Local reference files are not refreshed by an API cache bypass.

Progress uses actual events: started stages spin, completed stages receive a checkmark, and failed stages show an error. Pair identity is included in stage keys so N-drug collection does not overwrite previous pairs. No timed fake progress or private model reasoning is displayed.
