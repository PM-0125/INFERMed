from __future__ import annotations

from typing import Any

from src.domain.mechanism.entities import MechanismCluster, MechanismGraph
from src.domain.patient.entities import PatientContext


def detect_pgx_mechanisms(context: dict[str, Any], patient_context: PatientContext | None = None) -> MechanismGraph:
    research = ((context.get("signals") or {}).get("research_enrichment") or {})
    pgx = research.get("fda_pgx") or {}
    has_reference = bool(pgx.get("matches") or pgx.get("biomarkers"))
    has_patient_genotype = bool(patient_context and patient_context.genotypes)
    if not (has_reference or has_patient_genotype):
        return MechanismGraph()
    return MechanismGraph(
        clusters=[
            MechanismCluster(
                cluster_id="cluster:pgx",
                label="Pharmacogenomic context",
                risk_type="PGX",
                drivers=[item.gene for item in patient_context.genotypes] if patient_context else [],
                affected_drugs=[],
                confidence="low" if not has_patient_genotype else "medium",
            )
        ]
    )
