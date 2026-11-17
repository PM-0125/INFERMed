"""Scoped mechanistic interpretation; role overlap is not clinical severity."""

from collections.abc import Mapping


def biological_terms(value):
    if isinstance(value, Mapping):
        return {term for child in value.values() for term in biological_terms(child)}
    if isinstance(value, (list, tuple, set)):
        return {term for child in value for term in biological_terms(child)}
    return (
        {value.strip().upper()} if isinstance(value, str) and value.strip() else set()
    )


def interpret_pair(context, pair):
    enzymes = ((context.get("signals") or {}).get("mechanistic") or {}).get(
        "enzymes"
    ) or {}
    chains = []
    for actor, affected, actor_index, affected_index in (
        ("a", "b", 0, 1),
        ("b", "a", 1, 0),
    ):
        roles = enzymes.get(actor) or {}
        substrates = biological_terms((enzymes.get(affected) or {}).get("substrate"))
        for role, action in (("inhibitor", "inhibition"), ("inducer", "induction")):
            for protein in sorted(biological_terms(roles.get(role)) & substrates):
                chains.append(
                    {
                        "perpetrator": pair[actor_index],
                        "affected_drug": pair[affected_index],
                        "protein": protein,
                        "action": action,
                        "drug_scope": list(pair),
                        "knowledge_basis": "inference_from_retrieved_roles",
                        "role_source_path": "signals.mechanistic.enzymes",
                        "biological_change": (
                            "potentially reduced enzyme-mediated metabolism"
                            if role == "inhibitor"
                            else "potentially increased enzyme-mediated metabolism"
                        ),
                        "effect_caveat": "Exposure may rise with inhibition or fall with induction only if this is a relevant clearance route. Prodrug activation and active metabolites may change the direction of clinical effect; verify their role.",
                        "clinical_consequence": "not established",
                        "missing_links": [
                            "quantitative effect",
                            "clearance versus activation role",
                            "dose/route relevance",
                            "clinical outcome evidence",
                        ],
                    }
                )
    summary = str((context.get("pkpd") or {}).get("pk_summary") or "").lower()
    conflicts = []
    if chains and any(
        term in summary for term in ("no strong pk overlap", "no pk overlap")
    ):
        conflicts.append(
            "Summary reports no PK overlap but retrieved enzyme roles support a directional mechanism hypothesis. Clinical significance remains unresolved."
        )
    return {
        "drug_scope": list(pair),
        "causal_chains": chains,
        "conflicts": conflicts,
        "interpretation_limit": "No detected mechanism is not evidence of no interaction. Shared roles do not establish clinical severity.",
    }
