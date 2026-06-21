# INFERMed Notices

This notice explains boundaries that are intentionally separate from the repository code license.

## Project Copyright

Copyright (c) 2025-2026 Pranjul Mishra.

The INFERMed source code is licensed under the PolyForm Noncommercial License 1.0.0. See [LICENSE](LICENSE).

## Research And Medical Use

INFERMed is research software. It is not a medical device, not certified clinical decision support, and not a substitute for licensed clinical judgment, approved labeling, institutional policy, or patient-specific care.

Any clinician, researcher, reviewer, or institution using INFERMed must treat generated output as experimental assistance only.

## Third-Party Data

The repository license applies to INFERMed code and project-authored materials only. It does not grant rights to third-party datasets, APIs, publications, trademarks, logos, or model providers.

Users are responsible for complying with each upstream source's license, terms of service, attribution requirements, and access restrictions. See [data_license.md](data_license.md) for source links and project-specific data notes.

Known source categories include:

- OpenFDA / FAERS
- PubChem and PubChemRDF
- ChEMBL
- UniProt
- KEGG
- Reactome
- TWOSIDES
- DILIrank
- DICT / DICTRank
- DIQT
- DrugBank, when separately supplied by an authorized license holder

## Restricted Data

DrugBank-derived files and other restricted datasets must not be committed, redistributed, hosted publicly, or enabled in public deployments unless the user has explicit rights to do so.

The repository currently treats the following as restricted:

```text
data/duckdb/drugbank.parquet
```

This file is intentionally ignored by git and must be supplied manually only in environments with valid permission.

## Generated Caches And Logs

Generated caches and runtime logs may include source responses, interaction records, model outputs, or operational metadata. They should not be committed unless they are intentionally public-safe artifacts.

Recommended ignored runtime paths include:

```text
data/cache/
logs/
```

## Trademarks And Logos

Names and marks such as DrugBank, PubChem, OpenFDA, ChEMBL, UniProt, KEGG, Reactome, NVIDIA, and other third-party services belong to their respective owners. Mentioning them does not imply endorsement.

## Commercial Permission

Commercial use is not granted by this repository. Commercial use, hosted commercial services, clinical deployment, resale, sublicensing, or derivative commercial products require prior written permission from the copyright holder.
