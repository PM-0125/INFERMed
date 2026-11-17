# Reasoning and answer construction

INFERMed retrieves evidence for medication pairs, builds structured records, and constructs an audience-specific answer. Larger medication sets are decomposed into pairs. Combining pair findings does not establish a higher-order interaction.

## Evidence flow

1. Source adapters retrieve local and remote information. The RAG pipeline normalizes it into pair contexts, retaining source status and caveats.
2. The application builds evidence cards, drug profiles, mechanism candidates, and a decision record. Source scope and missing information remain relevant throughout processing.
3. A bounded enrichment pass can seek information for selected profile gaps. Defaults are one round, at most four calls, a 30-second budget, and a 10-second per-source timeout. This is application-controlled retrieval, not autonomous model browsing.
4. A copied reasoning input supplies evidence, profiles, decision, and supplied patient context to answer construction.
5. The application applies publication checks and audience-specific citation rendering before returning the answer. Analysis snapshots and events are persisted when the event store is enabled.

Drug-profile graphs organize retrieved information. They are distinct from external PubChem RDF graphs and from mechanism records derived by the application. A stored edge is not automatically a verified biological fact. Inspect its relationship, source link, and derivation before interpreting it.

PK role matching identifies candidate inhibitor/inducer-substrate mechanisms. It does not establish exposure magnitude, patient harm, or whether metabolism clears a drug or activates it. Decision scores are heuristics, not calibrated clinical risk estimates.

## Audience paths

| Path | Answer construction |
|---|---|
| Doctor/clinician with reasoning input | The model orders a bounded set of existing records. Backend code checks and renders them; model-generated clinical prose is not the answer source. A malformed ordering can trigger a bounded repair attempt. |
| Other initial audiences with reasoning input | Model-generated draft followed by bounded model review, then application publication checks. Review is not independent clinical validation. |
| Legacy inputs or mock generation | Separate compatibility/provider branches apply. Do not infer their behavior from the Doctor contract. |

Doctor follow-ups with reasoning input also use the record-based procedure with the follow-up question. Other follow-ups use their dedicated prompt path. Patient-context handling must preserve the distinction between supplied patient facts and observations reported in a study.

Evidence checks can withhold unsupported statements, but passing a check does not establish clinical appropriateness or agreement between sources. Retain review status, missing evidence, and limitations in downstream displays.

## Code map

| Component | Location |
|---|---|
| Configuration and source policy | `src/config/settings.py`, `src/config/data_policy.py` |
| Source adapters and normalized retrieval | `src/retrieval/`, `src/llm/rag_pipeline.py` |
| Profile and mechanism construction | `src/application/interaction_modeling.py`, `src/domain/reasoning/interpretation.py` |
| Bounded enrichment | `src/application/evidence_enrichment.py`, `src/infrastructure/tools/graph_enrichment.py` |
| Analysis and follow-up orchestration | `src/application/use_cases/` |
| Reasoning input | `src/application/reasoning_input.py` |
| Doctor records and source selection | `src/application/doctor_contract.py`, `src/application/source_selection.py` |
| Providers and model review | `src/llm/llm_interface.py`, `src/llm/review.py` |
| Evidence and publication checks | `src/domain/evidence/`, `src/domain/explanation/`, `src/domain/safety/` |
| API execution and presentation | `src/api/app.py`, `src/api/transformers.py`, `frontend/src/` |

When adding a source, verify its raw response, normalization, evidence scope, consumer, and final presentation. Do not assume that adding an adapter or prompt field makes its information available throughout the system. Keep domain interpretation out of provider transport code.
