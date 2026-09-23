# Graph Reasoning Architecture Correction

Status: proposed design for review; implementation has not started.
Branch: Graph-Reasoning-Architecture.

## Objective

Make graph reasoning drive explanations rather than being computed after them.
Preserve evidence-first, role-aware, patient-context-aware pair and N-drug analysis.
Implement all five approved priorities without implying clinical validation.
Do not read .env, publish datasets, change generation token limits, or move release tags.

## Current Defects

- `_flatten_values` treats a dictionary values view as a scalar. Empty PK
  overlap lists become a target named `dict_values([[], [], []])`, producing
  an unsupported PD hypothesis.
- `AnalyzeMedicationSet` generates the answer before building the profile
  graph, reasoning summary, and final decision.
- Prompt construction must explicitly consume the reasoning contract; merely
  adding a field to context is insufficient.
- Existing safety checks flag patterns but do not establish claim support.
- The About diagram overstates the running pipeline's ordering and agency.

## Alternatives

1. Fix only overlap parsing: smallest change, but leaves graph reasoning out
   of generation and does not satisfy the requested architecture.
2. Correct the existing orchestrator with typed reasoning inputs, bounded
   enrichment, and explicit verification states: recommended.
3. Replace it with unrestricted multi-agent orchestration: larger operational
   and evaluation burden without addressing evidence integrity first.

## Execution Contract

1. Normalize medicines and patient context; retrieve initial pair evidence.
2. Normalize evidence cards with stable IDs, provenance, pair scope, and
   explicit missing, failed, or empty source status.
3. Build profiles and compute pair and medication-set reasoning.
4. Plan a bounded enrichment pass for actionable evidence gaps; execute only
   registered, enabled, policy-permitted source adapters. Merge valid results
   by source identity and record provenance. Recompute graphs and reasoning.
5. Build the decision and an immutable explanation-input snapshot.
6. Generate the role-specific explanation from that snapshot.
7. Verify supported claims and contradictions; publish answer and verification
   status together. Persist the exact input snapshot and results.

Graph computation and enrichment must finish before the explanation call.
Every claim references supporting evidence IDs and affected medications.
Observed relationships, inferred mechanisms, and missing data stay distinct.
Overlap alone cannot establish interaction direction, magnitude, or severity.

## Priority 1: Overlap Integrity

Normalize supported containers explicitly, including mapping values views.
Accept only valid nonblank biological identifiers or labels at graph boundaries;
reject malformed objects rather than stringifying them into biological entities.
Empty containers, missing fields, whitespace, nulls, and malformed data must not
produce overlap signals. Genuine enzyme, transporter, target, and pathway
overlaps must remain intact. Preserve pair scope when merging N-drug profiles.

## Priorities 2 and 3: Reason Before Generation

Refactor the existing use case rather than creating a parallel pipeline.
Use explicit context fields for profile gaps, reasoning, evidence-linked
decisions, and supporting evidence cards. Both standard and compact prompt
paths consume the same contract. Follow-up reads the saved snapshot.
Budget prompt context by relevance without silently dropping contraindications,
conflicting evidence, or source limitations; log any omitted material.
Keep current model output-token settings unchanged.

## Priority 4: Verification and Streaming

Validate evidence-reference existence and medication scope. Detect explicit
risk contradictions, unsupported quantitative actions, unsupported named
mechanisms, association-as-causality claims, and unsupported certainty.
Lexical matching alone must never be described as proof of factual grounding.
Return supported, unsupported, contradicted, or unresolved claim status with
reasons and evidence references. Unresolved statements require review.
High-severity findings prevent a verified label, not silently substitute a
fabricated fallback. Preserve the draft for inspection with clear warnings.
Streaming text is visibly provisional until verification completes; final SSE
events carry verification status. Provider failures are explicit errors, not
successful assessments. No raw private model reasoning is displayed.

## Priority 5: Bounded Evidence Tools

Start with an application-controlled gap planner over existing adapters, not
an unrestricted LLM agent. Default to at most one enrichment round and four
additional deduplicated calls per analysis, with a configurable total deadline
and source timeouts. Do not retry empty evidence indefinitely or refetch all
sources. Preserve existing refresh and cache-version semantics.
Disabled, unlicensed, credential-missing, or unavailable sources remain explicit
gaps. No arbitrary URLs or model-generated executable code are accepted.
Expose source progress and completion without claiming a source succeeded when
it failed. Optional model planning is deferred; the UI must describe this
honestly as orchestrated evidence tools rather than autonomous agents.

## Verification Plan

- Regression tests first: empty dictionary values produce no nodes or PD
  hypothesis; valid nested overlaps remain correctly classified.
- Orchestrator spy tests: reasoning and decision exist before generator entry;
  the model sees the final enriched snapshot, including N-drug pair scopes.
- Standard/compact prompt tests prove evidence IDs, decision, hypotheses, and
  uncertainties survive formatting and relevant context budgeting.
- Enrichment tests cover success, no gaps, policy exclusions, deduplication,
  cache hits, timeout, malformed output, and exhausted budgets.
- Adversarial verifier tests cover invented IDs, wrong-pair citations,
  dose-number mismatch, risk contradiction, causal overclaim, missing evidence,
  and legitimate uncertainty statements to measure false positives.
- SSE and frontend tests distinguish provisional, verified, review-required,
  and provider-error states; follow-up uses the stored analysis snapshot.
- Run full pytest with dotenv loading disabled, Black, frontend lint and build.
- If the VM tunnel is available, run a small live smoke test. Report connection
  failures separately; never count mocks or fallback text as live model success.

## Acceptance and Limits

All five priorities must have tests and implementation evidence before being
reported complete. Keep known/unknown and two/three/four-drug fixtures, including
patient context. Automated verification is not a clinical correctness guarantee.
Update the About diagram to the actual implemented flow. Do not retrain, add a
second model, alter clinical risk calibration, or rerun the full benchmark in
this change. Those require separate evaluation after this architecture works.
