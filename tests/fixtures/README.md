# Synthetic regression fixtures

These project-authored examples are test inputs, not drug labels, patient data, or medical evidence. They are distributed under the repository license. No original DailyMed excerpts are included here.

- `synthetic_interaction_sections.xml`: fictional Alpha/Beta/Gamma/Delta observations in an SPL-shaped XML fragment. Exercises separate heading punctuation, section boundaries, shared parent qualifications and fallback when the supplied hierarchy is incomplete.
- `synthetic_renal_study.txt`: invented drug, study population, dose and measurements expressed in the narrow grammar accepted by the parser. Exercises preservation of population, units and source context, and rejection of additional unknown context. Required grammar phrases are dictated by the existing parser. This is not a reproduced full label paragraph.

The layout fingerprint test substitutes an expected digest only within that test. Production source fingerprints are unchanged. These tests verify parser behavior, not correspondence with a live label. Exact-source verification is separate from the portable test suite.
