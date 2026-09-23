# Graph Reasoning Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make validated graph reasoning and evidence-linked decisions precede and constrain explanation generation.

**Architecture:** Retain the existing application use case and domain objects. Add focused reasoning-input, enrichment, and claim-verification components; use existing source adapters and SQLite persistence. Keep one explanation model and application-controlled retrieval.

**Tech Stack:** Python, FastAPI, dataclasses, SQLite, pytest, React/TypeScript, Vite, Black.

**Spec:** `docs/superpowers/specs/2026-09-23-graph-reasoning-architecture-design.md`

## Global Constraints

- Branch: Graph-Reasoning-Architecture.
- Do not read .env, publish datasets, change generation token limits, or move release tags.
- Default to at most one enrichment round and four additional deduplicated calls per analysis, with a configurable total deadline and source timeouts.
- No raw private model reasoning is displayed.
- Automated verification is not a clinical correctness guarantee.
- Do not retrain, add a second model, alter clinical risk calibration, or rerun the full benchmark in this change.
- Tests disable dotenv loading using both INFERMED_SKIP_DOTENV=true and INFERMED_LOAD_DOTENV=false.
- Execution approved. See execution status below; original detailed checklists
  remain as the acceptance backlog rather than a claim that every item is done.

## Execution Status (2026-09-23)

- [x] False-overlap regression fixed, including malformed scalar containers.
- [x] Graph/decision contract precedes production generation and reaches prompts.
- [x] Bounded exact-ID ChEMBL/UniProt enrichment and cache evidence/history wired.
- [x] Scoped claim checks and honest review-required status exposed in UI.
- [x] Partial-stream failure does not become a successful assessment.
- [x] Unit/integration, frontend lint/build, and live synthetic-model checks run.
- [ ] Provider-aware input token budgeting and over-context rejection.
- [ ] Additional gap-to-source mappings and complete adapter edge-case matrix.
- [ ] Desktop/mobile visual verification of every provisional/error/review state.
- [ ] Independent review and clinical quality evaluation.

Implementation deviations: contract tests live in `test_reasoning_lifecycle.py`;
retrieval mapping lives in a focused infrastructure adapter instead of expanding
the initial planner; full evidence is retained rather than selectively truncated.
One cumulative implementation commit replaces the proposed per-task commits.

## Review Focus

1. Empty or malformed overlap containers must never become biological entities (Task 1).
2. Evidence for one pair must not support claims for a different pair in an N-drug set (Tasks 2 and 5).
3. Cache hits must preserve evidence cards, not only raw payloads (Task 3).
4. Enrichment failure must not erase usable original evidence or mutate persisted snapshots (Tasks 3 and 4).
5. Partial streamed text followed by provider failure must never become a successful or verified assessment (Task 6).

## Test Environment

Run Python commands from the repository root with these process-only settings:

```powershell
$env:INFERMED_SKIP_DOTENV='true'
$env:INFERMED_LOAD_DOTENV='false'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
```

## Task 1: Reject False Biological Overlap

**Modify:** `src/application/interaction_modeling.py`
**Create:** `tests/test_graph_reasoning_integrity.py`
**Interfaces:** Preserve `build_drug_profile_graph(...)` and `build_interaction_reasoning_record(...)`; change only normalization of their evidence inputs.

- [ ] Add this failing regression, then parameterize missing values, empty mappings, nested empty lists, whitespace, and malformed scalar objects:

```python
from src.application.interaction_modeling import (
    build_drug_profile_graph, build_interaction_reasoning_record,
)

def test_empty_pk_overlaps_do_not_create_pd_hypothesis():
    context = {"pkpd": {"pk_detail": {"overlaps": {
        "enzymes": [], "transporters": [], "other": [],
    }}}}
    graph = build_drug_profile_graph(
        analysis_id="test", drugs=["a", "b"], context=context, evidence_cards=[],
    )
    record = build_interaction_reasoning_record(
        analysis_id="test", drugs=["a", "b"], profile_graph=graph,
        context=context, evidence_cards=[],
    )
    assert not graph.shared_nodes("target")
    assert not record.hypotheses
    assert record.known_status == "unknown_insufficient_evidence"
```

- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_graph_reasoning_integrity.py -q`; confirm the reported defect fails.
- [ ] Normalize mappings and values views without arbitrary-object stringification. Drop blank/non-string biological terms at graph insertion. Keep valid nested enzyme/transporter/target/pathway values.
- [ ] Add positive CYP3A4/ABCB1 overlap tests, duplicate-term tests, and valid PD target/pathway tests; assert correct node types and pair scope.
- [ ] Rerun the new file and `tests/test_domain_architecture.py`; commit the focused fix.

## Task 2: Define the Explanation Input Contract

**Create:** `src/application/reasoning_input.py`, `tests/test_reasoning_input.py`
**Modify:** `src/llm/llm_interface.py`, `src/llm/prompt_templates.txt`
**Interfaces:** Add `build_reasoning_input(*, profile_graph, reasoning_record, ndrug_summary, decision, evidence_cards) -> dict[str, Any]` and `render_reasoning_input(payload: dict[str, Any]) -> str`. Store under `context['reasoning_input']` with schema_version=1.

- [ ] Test a contract containing decision, hypotheses, scope, evidence IDs, source limitations, and missing-profile fields. Assert modifying the caller's dictionary cannot mutate the snapshot.
- [ ] Add prompt tests for standard and compact builders using a sentinel evidence ID and hypothesis. Both must fail until the new contract is rendered.
- [ ] Serialize domain objects through their `to_dict()` methods and deep-copy into the contract. Preserve deterministic ordering and only referenced evidence plus safety-critical cards.
- [ ] Render a dedicated reasoning section in both prompt paths. Add instructions distinguishing observations, hypotheses, and unknowns and requiring claim-linked evidence references. Do not replace role guidance or change output-token settings.
- [ ] Test large-context handling: contraindications, conflicts, evidence IDs, and limitations survive; omissions are explicitly recorded, and a mandatory block that cannot fit produces an explicit input-size failure rather than silent truncation.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_reasoning_input.py tests/test_llm.py -q`; commit.

## Task 3: Implement Bounded Gap Enrichment

**Create:** `src/application/evidence_enrichment.py`, `tests/test_evidence_enrichment.py`
**Modify:** `src/application/tool_planner.py`, `src/infrastructure/tools/executor.py`, `src/infrastructure/persistence/cache_store.py`, `src/config/settings.py`
**Reuse:** `src/application/source_tools.py`, `src/infrastructure/tools/registry.py`, `src/infrastructure/retrieval/`

**Interfaces:** Add immutable `EnrichmentBudget(max_calls=4, max_rounds=1, deadline_s=30, source_timeout_s=10)` and `EnrichmentResult(results, skipped, exhausted)`. Add `enrich_profile_gaps(*, gaps, available_tools, attempted_inputs, executor, budget, clock) -> EnrichmentResult`. All requests remain `SourceToolInput`; all source results remain `SourceToolResult`.

- [ ] Write fake-adapter tests that count invocations for five eligible requests and assert at most four calls, one round, and no repeated canonical request keys.
- [ ] Use a fake clock to test deadline exhaustion before each call; enforce the remaining deadline in underlying client timeouts. Do not implement a timeout that leaves an uncontrolled background request running.
- [ ] Derive requests only from valid medication/protein IDs and supported gap-to-adapter mappings. Intersect registered tool policies with enabled/configured adapters. Record disabled, missing-credential, unknown-ID, and unavailable-tool reasons.
- [ ] Round-trip evidence cards and provenance through SQLite tool cache. Test uncached and cached `SourceToolResult` equivalence, including evidence IDs, raw payload hash, and error status.
- [ ] Preserve original evidence on failures. Merge validated additions by source identity and affected drug scope; keep conflicts and historical versions, not last-writer clinical truth.
- [ ] Add malformed-result, empty-success, cancellation/deadline, refresh, already-attempted, and no-gap tests. Never manufacture evidence for skipped tools.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_evidence_enrichment.py tests/test_sqlite_cache.py tests/test_tool_registry.py -q`; commit.

## Task 4: Wire the Correct Lifecycle

**Modify:** `src/application/use_cases/analyze_medication_set.py`, `src/application/use_cases/answer_followup.py`, `src/application/ports.py`, `src/api/app.py`
**Create:** `tests/test_reasoning_lifecycle.py`
**Reuse:** Task 2 contract, Task 3 enrichment, existing graph/reasoner/decision types.

- [ ] Add a generator spy that asserts `context['reasoning_input']` exists at invocation and contains the final decision and post-enrichment evidence. Capture progress events and assert reasoning precedes generation.
- [ ] Move normalized cards, profile graph, reasoning record, N-drug summary, and decision ahead of `_generate_medication_set_answer`. Run enrichment between initial graph and final reasoning; recompute affected pair decisions as well as aggregate reasoning.
- [ ] Remove the old post-generation duplicate construction. Preserve existing public response fields and pair-runner injection contracts.
- [ ] Deep-copy and persist the exact generation input with its hash. Verify model callbacks cannot modify stored evidence or decisions.
- [ ] Test two-, three-, and four-drug scopes. Never promote evidence confined to A+B to an established claim about A+C or the entire set.
- [ ] Test snapshot follow-up and patient-context updates. Recompute patient-sensitive decisions from the saved evidence when context changes; do not reuse a stale patient decision or silently refetch all sources.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_reasoning_lifecycle.py tests/test_domain_architecture.py tests/test_api_app.py tests/test_api_improvements.py -q`; commit.

## Task 5: Add Honest Claim-Level Verification

**Modify:** `src/domain/explanation/verifier.py`, `src/domain/safety/services.py`, `src/application/use_cases/analyze_medication_set.py`
**Create:** `tests/test_claim_verification.py`

**Interfaces:** Add `ClaimCheck(text, evidence_ids, drug_scope, status, reasons)` with status in supported/unsupported/contradicted/unresolved. Extend `GroundingVerification` with claims and status in checks_passed/review_required/failed. Retain existing fields for compatibility.

- [ ] Test invented evidence IDs and wrong-pair references; both must be unsupported even when the prose contains matching keywords.
- [ ] Extract sentence-level evidence references from the generated prose. Resolve them against the exact input snapshot, not all source records in the database.
- [ ] Detect explicit risk/decision conflicts and quantitative dose/monitoring mismatch against cited source text. Restrict numeric matching to the same relevant claim and preserve units; unrelated numeric matches cannot validate advice.
- [ ] Treat unverifiable semantic claims as unresolved. A citation's existence is not proof it supports the claim. Only narrowly checkable evidence-backed statements receive supported status.
- [ ] Test negated causality statements and uncertainty caveats to avoid flagging 'FAERS does not prove causality' as an overclaim. Test unsupported certainty and invented mechanisms.
- [ ] Aggregate findings without silently rewriting the explanation. High-severity findings prohibit checks_passed; retain the draft and actionable review reasons.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_claim_verification.py tests/test_domain_architecture.py -q`; commit.

## Task 6: Expose Provisional and Final Status Correctly

**Modify:** `src/api/app.py`, `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/App.css`
**Create:** `tests/test_reasoning_stream.py`

**Interfaces:** Keep progress/token/result/error SSE event types. Token events carry provisional text; final result includes `verification` with Task 5 status and findings. Error events never carry a successful assessment.

- [ ] Test event order with a fake generator: graph/enrichment/decision progress, provisional tokens, verification, final result. Inject a provider exception after a partial token and assert no result event follows.
- [ ] Surface verification in the API read model and frontend normalization, including nested compatibility responses. Preserve unknown/missing verification as unverified, never default-passed.
- [ ] Label live output 'Draft - verification pending'; final status says 'Automated checks passed' or 'Review required', with inspectable findings. Do not imply clinical validation.
- [ ] Prevent known provider-error text from being persisted or presented as a successful explanation. Preserve diagnostics separately, not as fallback medical prose.
- [ ] Update About architecture copy to application-orchestrated evidence tools and actual graph-before-generation ordering.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_reasoning_stream.py tests/test_api_app.py tests/test_api_transformers.py -q`, then frontend `npm.cmd run lint` and `npm.cmd run build`.
- [ ] Inspect desktop/mobile browser states for provisional, review-required, and failure results without submitting real patient information; commit.

## Task 7: Whole-Branch Verification and Handoff

**Modify:** `docs/developer_notes.md`; record private findings in `models/.codex/Issues/graph_reasoning_architecture.md` without committing them.

- [ ] Run `.venv/Scripts/python.exe -m black src tests scripts`, then `--check` with the same paths.
- [ ] Run `.venv/Scripts/python.exe -m pytest -q` under the test environment above. Investigate regressions; do not hide them with broad skips.
- [ ] Run clean frontend install in an isolated copy, lint, and build. Do not replace active frontend node_modules.
- [ ] Inspect the whole branch diff for stale post-generation graph logic, unsafely stringified evidence, policy bypass, incorrect pair scope, missing verification, and secrets/private files.
- [ ] If `http://127.0.0.1:11435/api/tags` is reachable, run a small synthetic-context smoke set: known pair, evidence-sparse pair, and N-drug case. Save results privately and inspect evidence use, uncertainty, verification, and event order. If unavailable, report live verification as blocked, not passed.
- [ ] Document configuration defaults, verification limits, tunnel dependency, and remaining issues. Report each of the five priorities separately against tests; do not claim medically validated reasoning.
- [ ] Commit only explicit public source/test/doc paths after `git diff --check`. Do not push or move a tag as part of this implementation handoff.

## Self-Review

The five approved priorities map to Tasks 1, 2/4, 2, 5/6, and 3/4 respectively.
Streaming, snapshot follow-up, cache integrity, source policies, and N-drug scope
have explicit tests. Live verification is conditional on the VM connection;
unit/integration verification is not. No new model or public data export is needed.
