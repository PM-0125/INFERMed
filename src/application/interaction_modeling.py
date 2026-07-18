from __future__ import annotations

import re
from typing import Any

from src.application.events import stable_hash
from src.domain.evidence.entities import EvidenceCard
from src.domain.profile.entities import DrugProfileGraph, ProfileEdge, ProfileNode
from src.domain.reasoning.entities import (
    InteractionHypothesis,
    InteractionReasoningRecord,
    KnownStatus,
    ReasoningSignal,
)

_SIDE_KEYS = ("a", "b")
_ENZYME_PREFIXES = ("CYP", "UGT", "NAT", "COMT", "MAO", "TPMT", "DPYD", "CES", "FMO")
_TRANSPORTER_PREFIXES = ("ABC", "SLC", "SLCO", "ABCB", "ABCC", "ABCG")
_REQUIRED_PROFILE_ELEMENTS = (
    "identifiers",
    "enzymes_or_transporters",
    "targets",
    "pathways",
    "adverse_events",
    "toxicity_markers",
    "label_context",
)


def build_drug_profile_graph(
    *,
    analysis_id: str,
    drugs: list[str],
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> DrugProfileGraph:
    builder = _ProfileGraphBuilder(analysis_id=analysis_id, drugs=drugs)
    for drug in drugs:
        builder.add_drug(drug)

    drug_sides = _drug_side_map(drugs, context)
    for drug, side in drug_sides.items():
        _add_identity_profile(builder, drug, side, context, evidence_cards)
        _add_drugcentral_profile(builder, drug, side, context, evidence_cards)
        _add_mechanistic_profile(builder, drug, side, context, evidence_cards)
        _add_adverse_event_profile(builder, drug, side, context, evidence_cards)
        _add_toxicity_profile(builder, drug, side, context, evidence_cards)
        _add_label_profile(builder, drug, side, context, evidence_cards)

    _add_shared_overlap_profile(builder, drugs, context, evidence_cards)
    return builder.to_graph()


def build_interaction_reasoning_record(
    *,
    analysis_id: str,
    drugs: list[str],
    profile_graph: DrugProfileGraph,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> InteractionReasoningRecord:
    signals: list[ReasoningSignal] = []
    hypotheses: list[InteractionHypothesis] = []
    limitations = _source_limitations(evidence_cards, context)

    direct_cards = [
        card
        for card in evidence_cards
        if card.source_name.lower() in {"canonical pk/pd", "openfda drug label"}
        and set(_lower_list(drugs)).issubset(set(_lower_list(card.drug_scope)))
    ]
    pair_signal_cards = [
        card
        for card in evidence_cards
        if _is_pair_signal_card(card, drugs)
    ]

    if direct_cards:
        evidence_ids = [card.evidence_id for card in direct_cards]
        signals.append(
            ReasoningSignal(
                category="known_pair",
                label="Direct known interaction evidence",
                support_level="established",
                drug_scope=drugs,
                evidence_ids=evidence_ids,
                limitations=limitations,
            )
        )
        hypotheses.append(
            _hypothesis(
                analysis_id,
                "known_pair",
                "Direct pair-level evidence exists and should anchor the explanation.",
                "established",
                drugs,
                evidence_ids,
                limitations,
            )
        )

    if pair_signal_cards:
        evidence_ids = [card.evidence_id for card in pair_signal_cards]
        signals.append(
            ReasoningSignal(
                category="adverse_event_signal",
                label="Pair-level pharmacovigilance signal",
                support_level="supported",
                drug_scope=drugs,
                evidence_ids=evidence_ids,
                limitations=["Associative signal; not proof of incidence or causality."],
            )
        )

    pk_nodes = profile_graph.shared_nodes("enzyme") + profile_graph.shared_nodes("transporter")
    if pk_nodes:
        evidence_ids = _unique(item for node in pk_nodes for item in node.evidence_ids)
        labels = ", ".join(node.label for node in pk_nodes[:6])
        signals.append(
            ReasoningSignal(
                category="pk_overlap",
                label=f"Shared PK-relevant proteins: {labels}",
                support_level="plausible",
                drug_scope=drugs,
                evidence_ids=evidence_ids,
                payload={"nodes": [node.to_dict() for node in pk_nodes[:12]]},
            )
        )
        hypotheses.append(
            _hypothesis(
                analysis_id,
                "pk_overlap",
                (
                    "A PK hypothesis is plausible because two or more drugs map to shared "
                    f"enzymes or transporters: {labels}."
                ),
                "plausible",
                drugs,
                evidence_ids,
                ["Shared PK proteins indicate plausibility, not direction or magnitude by themselves."],
            )
        )

    pd_nodes = profile_graph.shared_nodes("target") + profile_graph.shared_nodes("pathway")
    if pd_nodes:
        evidence_ids = _unique(item for node in pd_nodes for item in node.evidence_ids)
        labels = ", ".join(node.label for node in pd_nodes[:6])
        signals.append(
            ReasoningSignal(
                category="pd_overlap",
                label=f"Shared PD-relevant targets/pathways: {labels}",
                support_level="plausible",
                drug_scope=drugs,
                evidence_ids=evidence_ids,
                payload={"nodes": [node.to_dict() for node in pd_nodes[:12]]},
            )
        )
        hypotheses.append(
            _hypothesis(
                analysis_id,
                "pd_overlap",
                (
                    "A PD hypothesis is plausible because the drug profiles converge on "
                    f"shared targets or pathways: {labels}."
                ),
                "plausible",
                drugs,
                evidence_ids,
                ["Target/pathway overlap does not prove clinical interaction without stronger evidence."],
            )
        )

    toxicity_nodes = profile_graph.shared_nodes("adverse_event") + profile_graph.shared_nodes("toxicity_marker")
    if toxicity_nodes:
        evidence_ids = _unique(item for node in toxicity_nodes for item in node.evidence_ids)
        labels = ", ".join(node.label for node in toxicity_nodes[:6])
        signals.append(
            ReasoningSignal(
                category="toxicity_convergence",
                label=f"Convergent toxicity/adverse-event profile: {labels}",
                support_level="weak" if not pair_signal_cards else "supported",
                drug_scope=drugs,
                evidence_ids=evidence_ids,
                payload={"nodes": [node.to_dict() for node in toxicity_nodes[:12]]},
                limitations=["Convergent adverse-event profiles are hypothesis-generating unless pair evidence exists."],
            )
        )

    known_status = _known_status(signals)
    missing = profile_graph.missing_elements
    required_next_evidence = _next_evidence(missing, known_status)
    uncertainty = _uncertainty_factors(signals, missing, limitations)
    return InteractionReasoningRecord(
        analysis_id=analysis_id,
        drugs=drugs,
        known_status=known_status,
        signals=signals,
        hypotheses=hypotheses,
        missing_profile_elements=missing,
        uncertainty_factors=uncertainty,
        required_next_evidence=required_next_evidence,
    )


class _ProfileGraphBuilder:
    def __init__(self, *, analysis_id: str, drugs: list[str]):
        self.analysis_id = analysis_id
        self.drugs = _unique(drug.strip().lower() for drug in drugs if drug.strip())
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._counts: dict[str, dict[str, int]] = {
            drug: {category: 0 for category in _REQUIRED_PROFILE_ELEMENTS} for drug in self.drugs
        }
        self._limitations: list[str] = []

    def add_drug(self, drug: str) -> None:
        drug_key = drug.lower()
        self.add_node(
            node_id=f"drug:{_slug(drug_key)}",
            node_type="drug",
            label=drug,
            drug_scope=[drug_key],
            payload={"normalized_name": drug_key},
        )

    def add_profile_item(
        self,
        *,
        drug: str,
        category: str,
        node_type: str,
        label: str,
        edge_type: str,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        strength: str = "indirect",
    ) -> None:
        if not label:
            return
        drug_key = drug.lower()
        label = _display(label)
        node_id = f"{node_type}:{_slug(label)}"
        self.add_node(
            node_id=node_id,
            node_type=node_type,
            label=label,
            drug_scope=[drug_key],
            evidence_ids=evidence_ids or [],
            payload=payload or {},
        )
        self.add_edge(
            f"drug:{_slug(drug_key)}",
            node_id,
            edge_type,
            strength=strength,
            evidence_ids=evidence_ids or [],
            payload=payload or {},
        )
        if drug_key in self._counts and category in self._counts[drug_key]:
            self._counts[drug_key][category] += 1

    def add_shared_item(
        self,
        *,
        drugs: list[str],
        category: str,
        node_type: str,
        label: str,
        edge_type: str,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        for drug in drugs:
            self.add_profile_item(
                drug=drug,
                category=category,
                node_type=node_type,
                label=label,
                edge_type=edge_type,
                evidence_ids=evidence_ids,
                payload=payload,
                strength="inferred",
            )

    def add_node(
        self,
        *,
        node_id: str,
        node_type: str,
        label: str,
        drug_scope: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        row = self._nodes.setdefault(
            node_id,
            {
                "node_id": node_id,
                "type": node_type,
                "label": label,
                "drug_scope": [],
                "evidence_ids": [],
                "payload": {},
            },
        )
        row["drug_scope"] = _unique([*row["drug_scope"], *(drug_scope or [])])
        row["evidence_ids"] = _unique([*row["evidence_ids"], *(evidence_ids or [])])
        row["payload"].update(payload or {})

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        *,
        strength: str = "indirect",
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        key = (source_id, target_id, edge_type)
        row = self._edges.setdefault(
            key,
            {
                "source_id": source_id,
                "target_id": target_id,
                "type": edge_type,
                "strength": strength,
                "evidence_ids": [],
                "payload": {},
            },
        )
        row["evidence_ids"] = _unique([*row["evidence_ids"], *(evidence_ids or [])])
        row["payload"].update(payload or {})

    def to_graph(self) -> DrugProfileGraph:
        missing = {
            drug: [category for category, count in counts.items() if count == 0]
            for drug, counts in self._counts.items()
        }
        return DrugProfileGraph(
            analysis_id=self.analysis_id,
            drugs=self.drugs,
            nodes=[ProfileNode(**row) for row in self._nodes.values()],
            edges=[ProfileEdge(**row) for row in self._edges.values()],
            missing_elements={drug: gaps for drug, gaps in missing.items() if gaps},
            source_limitations=_unique(self._limitations),
        )


def _add_identity_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    evidence_ids = _card_ids(evidence_cards, drug=drug, source_contains=("rxnorm", "pubchem", "drugcentral"))
    drug_info = ((context.get("drugs") or {}).get(side) or {})
    for key, label in (
        ("pubchem_cid", "PubChem CID"),
        ("cid", "PubChem CID"),
        ("rxcui", "RxCUI"),
        ("drugbank_id", "DrugBank ID"),
    ):
        value = drug_info.get(key)
        if value:
            builder.add_profile_item(
                drug=drug,
                category="identifiers",
                node_type="identifier",
                label=f"{label}: {value}",
                edge_type="has_identifier",
                evidence_ids=evidence_ids,
                payload={"identifier_type": label, "value": value},
            )

    rxnorm = (((context.get("signals") or {}).get("clinical_reference") or {}).get("rxnorm") or {}).get(side) or {}
    if rxnorm.get("rxcui"):
        builder.add_profile_item(
            drug=drug,
            category="identifiers",
            node_type="identifier",
            label=f"RxCUI: {rxnorm['rxcui']}",
            edge_type="has_identifier",
            evidence_ids=_card_ids(evidence_cards, drug=drug, source_contains=("rxnorm",)),
            payload=rxnorm,
        )


def _add_drugcentral_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    enrichment = ((context.get("signals") or {}).get("research_enrichment") or {})
    drugcentral = ((enrichment.get("drugcentral") or {}).get(side) or {})
    evidence_ids = _card_ids(evidence_cards, drug=drug, source_contains=("drugcentral",))
    structure = drugcentral.get("structure") or {}
    for key, label in (("id", "DrugCentral ID"), ("cas", "CAS"), ("inchikey", "InChIKey")):
        value = structure.get(key)
        if value:
            builder.add_profile_item(
                drug=drug,
                category="identifiers",
                node_type="identifier",
                label=f"{label}: {value}",
                edge_type="has_identifier",
                evidence_ids=evidence_ids,
                payload={"identifier_type": label, "value": value},
            )
    if structure:
        formula = structure.get("formula") or structure.get("smiles") or structure.get("name") or drug
        builder.add_profile_item(
            drug=drug,
            category="identifiers",
            node_type="structure",
            label=f"Structure: {formula}",
            edge_type="has_structure",
            evidence_ids=evidence_ids,
            payload=structure,
        )

    for row in _list_dicts(drugcentral.get("targets"))[:16]:
        label = row.get("gene") or row.get("target_name") or row.get("target") or row.get("name")
        if not label:
            continue
        node_type, category, edge_type = _protein_node_type(str(label))
        builder.add_profile_item(
            drug=drug,
            category=category,
            node_type=node_type,
            label=str(label),
            edge_type=edge_type,
            evidence_ids=evidence_ids,
            payload=row,
        )


def _add_mechanistic_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    mechanistic = ((context.get("signals") or {}).get("mechanistic") or {})
    evidence_ids = _card_ids(evidence_cards, drug=drug, claim_contains=("mechanism", "pk", "pd"))

    for value in _terms_for_side(mechanistic, side, ("enzyme", "enzymes", "enzyme_ids", "enzyme_labels")):
        node_type, category, edge_type = _protein_node_type(value)
        builder.add_profile_item(
            drug=drug,
            category=category,
            node_type=node_type,
            label=value,
            edge_type=edge_type,
            evidence_ids=evidence_ids,
        )

    for value in _terms_for_side(mechanistic, side, ("target", "targets", "target_ids", "target_labels", "proteins")):
        builder.add_profile_item(
            drug=drug,
            category="targets",
            node_type="target",
            label=value,
            edge_type="has_target",
            evidence_ids=evidence_ids,
        )

    for value in _terms_for_side(mechanistic, side, ("pathway", "pathways", "pathway_ids", "pathway_labels")):
        builder.add_profile_item(
            drug=drug,
            category="pathways",
            node_type="pathway",
            label=value,
            edge_type="participates_in_pathway",
            evidence_ids=evidence_ids,
        )


def _add_adverse_event_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    signals = context.get("signals") or {}
    faers = signals.get("faers") or {}
    tabular = signals.get("tabular") or {}
    evidence_ids = _card_ids(evidence_cards, drug=drug, source_contains=("faers", "twosides", "offsides", "sider"))
    for label, count in _reaction_pairs(faers.get(f"top_reactions_{side}"))[:10]:
        builder.add_profile_item(
            drug=drug,
            category="adverse_events",
            node_type="adverse_event",
            label=label,
            edge_type="associated_with_adverse_event",
            evidence_ids=evidence_ids,
            payload={"count": count, "source": "OpenFDA FAERS"},
        )
    for key in (f"side_effects_{side}", f"side_effects_{drug.lower()}", f"{side}_side_effects"):
        for label, score in _reaction_pairs(tabular.get(key))[:10]:
            builder.add_profile_item(
                drug=drug,
                category="adverse_events",
                node_type="adverse_event",
                label=label,
                edge_type="associated_with_adverse_event",
                evidence_ids=evidence_ids,
                payload={"score": score, "source": "local_side_effects"},
            )


def _add_toxicity_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    tabular = ((context.get("signals") or {}).get("tabular") or {})
    evidence_ids = _card_ids(evidence_cards, drug=drug, source_contains=("dili", "diqt", "dict", "internal"))
    for key, label in (
        (f"dili_{side}", "DILI"),
        (f"dili_score_{side}", "DILI"),
        (f"diqt_{side}", "DIQT"),
        (f"dict_{side}", "DICT"),
        (f"dictrank_{side}", "DICT"),
    ):
        value = tabular.get(key)
        if value is None:
            continue
        builder.add_profile_item(
            drug=drug,
            category="toxicity_markers",
            node_type="toxicity_marker",
            label=f"{label}: {value}",
            edge_type="has_toxicity_signal",
            evidence_ids=evidence_ids,
            payload={"metric": label, "value": value},
        )


def _add_label_profile(
    builder: _ProfileGraphBuilder,
    drug: str,
    side: str,
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    label = (
        (((context.get("signals") or {}).get("clinical_reference") or {}).get("openfda_label") or {}).get(side) or {}
    )
    sections = label.get("sections") or {}
    evidence_ids = _card_ids(evidence_cards, drug=drug, source_contains=("label", "daily", "fda"))
    for section_name in list(sections)[:8]:
        builder.add_profile_item(
            drug=drug,
            category="label_context",
            node_type="label_section",
            label=str(section_name),
            edge_type="has_label_context",
            evidence_ids=evidence_ids,
            payload={"source": "openFDA label"},
        )


def _add_shared_overlap_profile(
    builder: _ProfileGraphBuilder,
    drugs: list[str],
    context: dict[str, Any],
    evidence_cards: list[EvidenceCard],
) -> None:
    pkpd = context.get("pkpd") or {}
    pk_detail = pkpd.get("pk_detail") or {}
    pd_detail = pkpd.get("pd_detail") or {}
    evidence_ids = _card_ids(evidence_cards, claim_contains=("mechanism", "pk", "pd"))

    for value in _flatten_values((pk_detail.get("overlaps") or {}).values()):
        node_type, category, edge_type = _protein_node_type(str(value))
        builder.add_shared_item(
            drugs=drugs,
            category=category,
            node_type=node_type,
            label=str(value),
            edge_type=edge_type,
            evidence_ids=evidence_ids,
            payload={"source": "pk_overlap"},
        )

    for value in _flatten_values(pd_detail.get("overlap_targets") or []):
        builder.add_shared_item(
            drugs=drugs,
            category="targets",
            node_type="target",
            label=str(value),
            edge_type="has_target",
            evidence_ids=evidence_ids,
            payload={"source": "pd_overlap"},
        )
    for value in _flatten_values(pd_detail.get("overlap_pathways") or []):
        builder.add_shared_item(
            drugs=drugs,
            category="pathways",
            node_type="pathway",
            label=str(value),
            edge_type="participates_in_pathway",
            evidence_ids=evidence_ids,
            payload={"source": "pd_overlap"},
        )


def _drug_side_map(drugs: list[str], context: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    context_drugs = context.get("drugs") or {}
    for side in _SIDE_KEYS:
        raw = context_drugs.get(side) or {}
        name = str(raw.get("name") or raw.get("query") or "").strip().lower()
        for drug in drugs:
            if drug.lower() == name:
                mapped[drug.lower()] = side
    for idx, drug in enumerate(drugs[:2]):
        mapped.setdefault(drug.lower(), _SIDE_KEYS[idx])
    return mapped


def _protein_node_type(label: str) -> tuple[str, str, str]:
    upper = label.upper()
    if upper.startswith(_TRANSPORTER_PREFIXES):
        return "transporter", "enzymes_or_transporters", "transported_by"
    if upper.startswith(_ENZYME_PREFIXES):
        return "enzyme", "enzymes_or_transporters", "metabolized_by"
    return "target", "targets", "has_target"


def _terms_for_side(mechanistic: dict[str, Any], side: str, bases: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for base in bases:
        for key in (f"{base}_{side}", f"{base}s_{side}", f"{side}_{base}", f"{side}_{base}s"):
            values.extend(str(item) for item in _flatten_values(mechanistic.get(key)) if _display(item))
    return _unique(values)


def _reaction_pairs(value: Any) -> list[tuple[str, Any]]:
    rows = []
    if value is None:
        source_rows: list[Any] = []
    elif isinstance(value, dict):
        source_rows = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        source_rows = list(value)
    else:
        source_rows = [value]
    for item in source_rows:
        if isinstance(item, dict):
            label = item.get("reaction") or item.get("side_effect") or item.get("label") or item.get("name")
            score = item.get("count") or item.get("prr") or item.get("score")
        elif isinstance(item, (list, tuple)) and item:
            label = item[0]
            score = item[1] if len(item) > 1 else None
        else:
            label = item
            score = None
        if _display(label):
            rows.append((_display(label), score))
    return rows


def _known_status(signals: list[ReasoningSignal]) -> KnownStatus:
    if any(signal.category == "known_pair" and signal.support_level == "established" for signal in signals):
        return "known_direct"
    if any(signal.category == "adverse_event_signal" for signal in signals):
        return "known_signal_supported"
    if any(signal.category in {"pk_overlap", "pd_overlap", "toxicity_convergence"} for signal in signals):
        return "unknown_mechanistically_plausible"
    return "unknown_insufficient_evidence"


def _is_pair_signal_card(card: EvidenceCard, drugs: list[str]) -> bool:
    if not set(_lower_list(drugs)).issubset(set(_lower_list(card.drug_scope))):
        return False
    source = card.source_name.lower()
    if source == "twosides":
        return True
    if source == "openfda faers":
        return bool((card.payload or {}).get("combo_reactions"))
    return False


def _hypothesis(
    analysis_id: str,
    mechanism_type: str,
    statement: str,
    support_level: str,
    affected_drugs: list[str],
    evidence_ids: list[str],
    limitations: list[str],
) -> InteractionHypothesis:
    return InteractionHypothesis(
        hypothesis_id="hyp_" + stable_hash(
            {
                "analysis_id": analysis_id,
                "mechanism_type": mechanism_type,
                "statement": statement,
            }
        )[:16],
        mechanism_type=mechanism_type,
        statement=statement,
        support_level=support_level,  # type: ignore[arg-type]
        affected_drugs=affected_drugs,
        evidence_ids=evidence_ids,
        limitations=limitations,
    )


def _next_evidence(missing: dict[str, list[str]], known_status: KnownStatus) -> list[str]:
    needed: list[str] = []
    if known_status.startswith("unknown"):
        needed.append("Direct pair evidence from label, clinical study, or curated DDI source")
    categories = sorted({category for values in missing.values() for category in values})
    labels = {
        "identifiers": "stronger identity resolution",
        "enzymes_or_transporters": "enzyme/transporter profile",
        "targets": "target profile",
        "pathways": "pathway profile",
        "adverse_events": "single-drug adverse-event profile",
        "toxicity_markers": "organ-toxicity risk profile",
        "label_context": "regulatory label context",
    }
    needed.extend(labels.get(category, category) for category in categories)
    return _unique(needed)


def _uncertainty_factors(
    signals: list[ReasoningSignal],
    missing: dict[str, list[str]],
    limitations: list[str],
) -> list[str]:
    factors = list(limitations)
    if missing:
        factors.append("One or more drug profiles are incomplete.")
    if not signals:
        factors.append("No direct, signal, or mechanistic reasoning signal was produced.")
    if any(signal.category in {"adverse_event_signal", "toxicity_convergence"} for signal in signals):
        factors.append("Adverse-event signals are associative unless supported by stronger clinical evidence.")
    return _unique(factors)[:12]


def _source_limitations(evidence_cards: list[EvidenceCard], context: dict[str, Any]) -> list[str]:
    rows = []
    for card in evidence_cards:
        rows.extend(card.limitations)
    rows.extend(context.get("caveats") or [])
    return _unique(str(row) for row in rows if str(row).strip())


def _card_ids(
    cards: list[EvidenceCard],
    *,
    drug: str | None = None,
    source_contains: tuple[str, ...] = (),
    claim_contains: tuple[str, ...] = (),
) -> list[str]:
    drug_key = drug.lower() if drug else None
    ids = []
    for card in cards:
        source = card.source_name.lower()
        claim = f"{card.claim_type} {card.claim_text}".lower()
        scope = {item.lower() for item in card.drug_scope}
        if drug_key and drug_key not in scope:
            continue
        if source_contains and not any(item in source for item in source_contains):
            continue
        if claim_contains and not any(item in claim for item in claim_contains):
            continue
        ids.append(card.evidence_id)
    return _unique(ids)


def _flatten_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        rows = []
        for item in value.values():
            rows.extend(_flatten_values(item))
        return rows
    if isinstance(value, (list, tuple, set)):
        rows = []
        for item in value:
            rows.extend(_flatten_values(item))
        return rows
    return [value]


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _lower_list(values: list[str]) -> list[str]:
    return [value.lower() for value in values]


def _display(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "unknown"


def _unique(values: Any) -> list[Any]:
    seen = set()
    rows = []
    for value in values:
        key = str(value).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(value)
    return rows
