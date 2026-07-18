# INFERMed Reasoning Architecture

INFERMed should treat retrieval, reasoning, explanation, and safety as separate layers. The LLM is allowed to translate structured evidence into role-specific language, but it should not be the source of truth for retrieved facts.

## Layers

1. **Evidence acquisition**
   - Public APIs, local datasets, licensed local snapshots, and future graph stores.
   - Output must preserve source, provenance, limitations, and data mode.

2. **Drug profile graph**
   - One profile per drug.
   - Nodes include identifiers, structures, enzymes, transporters, targets, pathways, adverse events, toxicity markers, and label sections.
   - Missing profile elements are explicit data gaps, not silently ignored.

3. **Pair / N-drug reasoning**
   - Computes direct known evidence, PK overlap, PD overlap, toxicity convergence, pharmacovigilance signals, and uncertainty.
   - Unknown combinations are hypothesis-level unless direct evidence is present.
   - Pair-level evidence must not be confused with single-drug adverse-event evidence.

4. **Agentic LLM explanation**
   - Receives normalized JSON records and evidence cards.
   - Writes the answer for the selected audience.
   - Does not fetch data directly and does not invent source facts.

5. **Zero-trust safety gate**
   - Flags unsupported dose guidance, causal overclaims from associative signals, unscoped hypotheses, and missing-evidence disclosure failures.
   - This is deterministic first. A model critic can be added later after the schema stabilizes.

6. **Evidence-first UI**
   - Shows the AI explanation beside evidence, profile gaps, source limitations, and reasoning status.

## Current Code Boundaries

- `src/domain/profile`: drug profile graph entities.
- `src/domain/reasoning`: interaction reasoning records and hypothesis entities.
- `src/domain/safety`: safety report entities and zero-trust validation services.
- `src/application/interaction_modeling.py`: current bridge from legacy RAG context into the new profile and reasoning records.
- `src/application/use_cases/analyze_medication_set.py`: lifecycle orchestration and audit events.
- `src/llm/rag_pipeline.py`: legacy compatibility pipeline; keep new domain logic out of this file unless deliberately migrating it.

## Migration Rule

New sources should first map into evidence cards and drug profile graph nodes. Reasoning should consume those structured records. Prompt text should be the final presentation step, not the place where source semantics are invented.
