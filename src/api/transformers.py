from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


RiskLevel = str

FAERS_CAVEAT = (
    "FAERS/OpenFDA reports are associative reporting signals, not incidence rates "
    "or proof of causality."
)

# ── Retrieval & display limits ────────────────────────────────────────────────
MAX_REACTION_ROWS: int = 10
MAX_ALIASES: int = 8
MAX_TARGET_VALUES: int = 8
MAX_ENZYME_PER_ROLE: int = 6
MAX_SIDE_EFFECTS: int = 8

# ── Risk scoring thresholds ───────────────────────────────────────────────────
PRR_HIGH: float = 2.0
PRR_MODERATE: float = 1.5
TOXICITY_SCORE_HIGH: float = 0.75
RISK_SCORE_HIGH: int = 4
RISK_SCORE_MODERATE: int = 2


def build_interaction_result(
    rag_output: Dict[str, Any],
    *,
    fallback_drug_a: str,
    fallback_drug_b: str,
) -> Dict[str, Any]:
    context = rag_output.get("context") or {}
    answer = rag_output.get("answer") or {}
    text = str(answer.get("text") or "").strip()

    drugs = context.get("drugs") or {}
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    mechanistic = signals.get("mechanistic") or {}
    research_enrichment = signals.get("research_enrichment") or {}
    pkpd = context.get("pkpd") or {}

    risk_summary = calculate_risk_summary(context)
    risk_level, risk_label, confidence = risk_summary

    medication_set_drugs = drugs.get("set") if isinstance(drugs.get("set"), list) else None
    if medication_set_drugs:
        mapped_drugs = [
            map_drug_identity(raw if isinstance(raw, dict) else {"name": str(raw)}, str((raw or {}).get("name") if isinstance(raw, dict) else raw))
            for raw in medication_set_drugs
        ]
    else:
        mapped_drugs = [
            map_drug_identity(drugs.get("a") or {}, fallback_drug_a),
            map_drug_identity(drugs.get("b") or {}, fallback_drug_b),
        ]

    return {
        "drugs": mapped_drugs,
        "risk": {
            "level": risk_level,
            "label": risk_label,
            "interactionClass": infer_interaction_class(pkpd),
            "confidence": confidence,
        },
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceBadges": build_source_badges(context),
        "assessment": parse_assessment_sections(text),
        "evidence": {
            "overview": build_overview_card(context, risk_summary),
            "openfda": build_openfda_card(faers),
            "internal": build_internal_card(tabular, pkpd),
            "mechanisms": build_mechanisms_card(mechanistic, pkpd, research_enrichment),
            "sources": build_sources_list(context),
            "references": build_references(context),
        },
    }


def map_drug_identity(raw: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    ids = raw.get("ids") or {}
    out: Dict[str, Any] = {
        "name": str(raw.get("name") or fallback_name).strip() or fallback_name,
    }

    pubchem_cid = ids.get("pubchem_cid") or ids.get("pubchem") or ids.get("cid")
    drugbank_id = ids.get("drugbank") or ids.get("drugbank_id")
    rxcui = ids.get("rxcui") or ids.get("rxnorm")
    if pubchem_cid:
        out["pubchemCid"] = str(pubchem_cid)
    if drugbank_id:
        out["drugbankId"] = str(drugbank_id)
    if rxcui:
        out["rxcui"] = str(rxcui)

    aliases = [str(item).strip() for item in raw.get("synonyms", []) or [] if str(item).strip()]
    if aliases:
        out["aliases"] = aliases[:MAX_ALIASES]
    return out


def calculate_risk_summary(context: Dict[str, Any]) -> Tuple[RiskLevel, str, str]:
    if not has_decision_evidence(context):
        return "unknown", "Insufficient Evidence", "No usable evidence returned by active sources"

    pkpd = context.get("pkpd") or {}
    signals = context.get("signals") or {}
    faers = signals.get("faers") or {}
    tabular = signals.get("tabular") or {}

    score = 0
    pk_detail = pkpd.get("pk_detail") or {}
    canonical = pk_detail.get("canonical_interaction")
    if canonical:
        score += 3
        severity = str(canonical.get("severity") or "").lower()
        if severity in {"high", "severe", "contraindicated", "major"}:
            score += 1

    overlaps = pk_detail.get("overlaps") or {}
    if overlaps.get("inhibition") or overlaps.get("induction"):
        score += 2
    if overlaps.get("shared_substrate"):
        score += 1

    pd_detail = pkpd.get("pd_detail") or {}
    if pd_detail.get("overlap_targets") or pd_detail.get("overlap_pathways"):
        score += 1

    if faers.get("combo_reactions"):
        score += 1

    prr = _coerce_float(tabular.get("prr"))
    if prr is not None:
        if prr > PRR_HIGH:
            score += 2
        elif prr > PRR_MODERATE:
            score += 1

    dili_values = [_coerce_float(tabular.get(key)) for key in ("dili_a", "dili_b")]
    dict_values = [_coerce_float(tabular.get(key)) for key in ("dict_a", "dict_b")]
    diqt_values = [_coerce_float(tabular.get(key)) for key in ("diqt_a", "diqt_b")]
    if any(value is not None and value >= TOXICITY_SCORE_HIGH for value in dili_values + dict_values + diqt_values):
        score += 1

    if score >= RISK_SCORE_HIGH:
        return "high", "High Risk", "Multiple convergent evidence signals"
    if score >= RISK_SCORE_MODERATE:
        return "moderate", "Moderate Risk", "Mechanistic or signal evidence present"
    return "low", "Low Risk", "Evidence present, but no strong risk signal detected"


def has_decision_evidence(context: Dict[str, Any]) -> bool:
    signals = context.get("signals") or {}
    tabular = signals.get("tabular") or {}
    faers = signals.get("faers") or {}
    mechanistic = signals.get("mechanistic") or {}
    pkpd = context.get("pkpd") or {}

    if _coerce_float(tabular.get("prr")) is not None:
        return True
    if any(_coerce_float(tabular.get(key)) is not None for key in ("dili_a", "dili_b", "dict_a", "dict_b", "diqt_a", "diqt_b")):
        return True
    if any(tabular.get(key) for key in ("side_effects_a", "side_effects_b", "side_effects_pair")):
        return True
    if tabular.get("nci_almanac"):
        return True
    if any(faers.get(key) for key in ("top_reactions_a", "top_reactions_b", "combo_reactions")):
        return True

    pk_detail = pkpd.get("pk_detail") or {}
    if pk_detail.get("canonical_interaction"):
        return True
    overlaps = pk_detail.get("overlaps") or {}
    if any(overlaps.get(key) for key in ("inhibition", "induction", "shared_substrate")):
        return True

    pd_detail = pkpd.get("pd_detail") or {}
    if pd_detail.get("overlap_targets") or pd_detail.get("overlap_pathways"):
        return True
    if any(mechanistic.get(key) for key in ("targets_a", "targets_b", "pathways_a", "pathways_b", "common_pathways")):
        return True
    if any(
        mechanistic.get(key)
        for key in (
            "uniprot_ids_a",
            "uniprot_ids_b",
            "uniprot_targets_a",
            "uniprot_targets_b",
            "kegg_pathways_a",
            "kegg_pathways_b",
            "kegg_common_pathways",
            "kegg_enzymes_a",
            "kegg_enzymes_b",
            "reactome_pathways_a",
            "reactome_pathways_b",
        )
    ):
        return True
    if _has_chembl_content(mechanistic.get("chembl_enrichment")):
        return True
    return False


def infer_interaction_class(pkpd: Dict[str, Any]) -> str:
    pk_detail = pkpd.get("pk_detail") or {}
    canonical = pk_detail.get("canonical_interaction") or {}
    mechanism = str(canonical.get("mechanism") or "").strip()
    if mechanism:
        return mechanism

    overlaps = pk_detail.get("overlaps") or {}
    classes: List[str] = []
    if overlaps.get("inhibition"):
        classes.append("PK inhibition")
    if overlaps.get("induction"):
        classes.append("PK induction")
    if overlaps.get("shared_substrate"):
        classes.append("shared substrate competition")

    pd_detail = pkpd.get("pd_detail") or {}
    if pd_detail.get("overlap_targets") or pd_detail.get("overlap_pathways"):
        classes.append("PD overlap")

    return " + ".join(classes) if classes else "Evidence-backed interaction review"


def parse_assessment_sections(text: str) -> List[Dict[str, str]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return [{"title": "Assessment", "body": "No generated assessment was returned."}]

    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", cleaned))
    if not matches:
        return [{"title": "Assessment", "body": cleaned}]

    sections: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        title = match.group(1).strip()
        body = cleaned[start:end].strip()
        if title and body:
            sections.append({"title": title, "body": body})
    return sections or [{"title": "Assessment", "body": cleaned}]


def build_source_badges(context: Dict[str, Any]) -> List[str]:
    badges = ["AI assessment"]
    sources = context.get("sources") or {}
    label_by_key = {
        "duckdb": "Internal evidence",
        "qlever": "PubChem RDF",
        "openfda": "OpenFDA",
        "apis": "Public APIs",
        "canonical": "Canonical PK/PD",
        "semantic": "Semantic search",
        "reranking": "Reranking",
    }
    for key, label in label_by_key.items():
        if key == "apis" and sources.get(key):
            badges.extend(_strings(sources.get(key))[:4])
        elif sources.get(key):
            badges.append(label)
    return list(dict.fromkeys(badges))


def build_overview_card(context: Dict[str, Any], risk_summary: Tuple[RiskLevel, str, str]) -> Dict[str, Any]:
    risk_level, risk_label, confidence = risk_summary
    pkpd = context.get("pkpd") or {}
    meta = context.get("meta") or {}
    return {
        "metrics": [
            {"label": "Risk", "value": risk_label, "tone": risk_level},
            {"label": "Evidence mode", "value": str(meta.get("data_mode") or "configured"), "tone": "neutral"},
            {"label": "Confidence", "value": confidence, "tone": "neutral"},
        ],
        "rows": [
            {
                "title": "PK summary",
                "description": str(pkpd.get("pk_summary") or "No PK summary returned."),
                "meta": "PK/PD",
            },
            {
                "title": "PD summary",
                "description": str(pkpd.get("pd_summary") or "No PD summary returned."),
                "meta": "PK/PD",
            },
        ],
    }


def build_openfda_card(faers: Dict[str, Any]) -> Dict[str, Any]:
    top_a = _pairs(faers.get("top_reactions_a"))
    top_b = _pairs(faers.get("top_reactions_b"))
    combo = _pairs(faers.get("combo_reactions"))
    top_a_rows, shown_a = _reaction_rows("Drug A FAERS reactions", top_a)
    top_b_rows, shown_b = _reaction_rows("Drug B FAERS reactions", top_b)
    combo_rows, shown_combo = _reaction_rows("Combination FAERS reactions", combo)
    rows = top_a_rows + top_b_rows + combo_rows
    if not rows:
        rows = [
            {
                "title": "No FAERS reactions returned",
                "description": "No OpenFDA rows were returned for this query or OpenFDA is disabled.",
                "meta": "No rows",
            }
        ]
    return {
        "metrics": [
            {"label": "Drug A rows", "value": _shown_of_total(shown_a, len(top_a)), "tone": "neutral"},
            {"label": "Drug B rows", "value": _shown_of_total(shown_b, len(top_b)), "tone": "neutral"},
            {"label": "Combo rows", "value": _shown_of_total(shown_combo, len(combo)), "tone": "moderate" if combo else "neutral"},
        ],
        "rows": rows,
        "caveat": _subset_note(
            (shown_a, len(top_a), "Drug A"),
            (shown_b, len(top_b), "Drug B"),
            (shown_combo, len(combo), "combination"),
        )
        + " "
        + FAERS_CAVEAT,
    }


def build_internal_card(tabular: Dict[str, Any], pkpd: Dict[str, Any]) -> Dict[str, Any]:
    canonical = (pkpd.get("pk_detail") or {}).get("canonical_interaction") or {}
    rows: List[Dict[str, str]] = []
    if canonical:
        rows.append(
            {
                "title": "Canonical PK/PD rule",
                "description": str(canonical.get("mechanism") or canonical),
                "meta": str(canonical.get("evidence_level") or "canonical"),
            }
        )

    side_a = _strings(tabular.get("side_effects_a"))
    side_b = _strings(tabular.get("side_effects_b"))
    if side_a:
        rows.append({"title": "Drug A side-effect signals", "description": ", ".join(side_a[:MAX_SIDE_EFFECTS]), "meta": _returned_note(len(side_a))})
    if side_b:
        rows.append({"title": "Drug B side-effect signals", "description": ", ".join(side_b[:MAX_SIDE_EFFECTS]), "meta": _returned_note(len(side_b))})
    nci_rows = _nci_rows(tabular.get("nci_almanac"))
    rows.extend(nci_rows)
    if not rows:
        rows.append({"title": "No internal rows returned", "description": "No public parquet or canonical rows were available for this query.", "meta": "Internal"})

    prr = tabular.get("prr")
    return {
        "metrics": [
            {"label": "PRR", "value": _display(prr, "Not available"), "tone": _prr_tone(prr)},
            {"label": "DILI", "value": _score_pair(tabular, "dili_a", "dili_b"), "tone": "neutral"},
            {"label": "DICT", "value": _score_pair(tabular, "dict_a", "dict_b"), "tone": "neutral"},
            {"label": "DIQT", "value": _score_pair(tabular, "diqt_a", "diqt_b"), "tone": "neutral"},
            {"label": "NCI", "value": _returned_note(len(tabular.get("nci_almanac") or [])), "tone": "neutral"},
        ],
        "rows": rows,
    }


def build_mechanisms_card(
    mechanistic: Dict[str, Any],
    pkpd: Dict[str, Any],
    research_enrichment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    enzymes = mechanistic.get("enzymes") or {}
    rows: List[Dict[str, str]] = []
    for side_key, label in (("a", "Drug A enzymes"), ("b", "Drug B enzymes")):
        role_map = enzymes.get(side_key) or {}
        parts = []
        for role in ("substrate", "inhibitor", "inducer"):
            values = _strings(role_map.get(role))
            if values:
                parts.append(f"{role}: {', '.join(values[:MAX_ENZYME_PER_ROLE])}")
        if parts:
            rows.append({"title": label, "description": "; ".join(parts), "meta": "Enzymes"})

    for key, title in (
        ("targets_a", "Drug A targets"),
        ("targets_b", "Drug B targets"),
        ("common_pathways", "Common pathways"),
        ("pathways_a", "Drug A pathways"),
        ("pathways_b", "Drug B pathways"),
    ):
        values = _strings(mechanistic.get(key))
        if values:
            rows.append(
                {
                    "title": title,
                    "description": ", ".join(values[:MAX_TARGET_VALUES]),
                    "meta": _mechanism_source_note(key, mechanistic, len(values)),
                }
            )

    for key, title, meta in (
        ("uniprot_ids_a", "Drug A UniProt accessions", "UniProt"),
        ("uniprot_ids_b", "Drug B UniProt accessions", "UniProt"),
    ):
        values = _strings(mechanistic.get(key))
        if values:
            rows.append({"title": title, "description": ", ".join(values[:MAX_TARGET_VALUES]), "meta": meta})

    for key, title, meta in (
        ("uniprot_targets_a", "Drug A UniProt target details", "UniProt"),
        ("uniprot_targets_b", "Drug B UniProt target details", "UniProt"),
    ):
        values = _protein_labels(mechanistic.get(key))
        if values:
            rows.append({"title": title, "description": ", ".join(values[:MAX_TARGET_VALUES]), "meta": meta})

    for key, title, meta in (
        ("kegg_pathways_a", "Drug A KEGG pathway IDs", "KEGG"),
        ("kegg_pathways_b", "Drug B KEGG pathway IDs", "KEGG"),
        ("kegg_common_pathways", "Common KEGG pathway IDs", "KEGG"),
        ("reactome_pathways_a", "Drug A Reactome pathways", "Reactome"),
        ("reactome_pathways_b", "Drug B Reactome pathways", "Reactome"),
    ):
        values = _pathway_labels(mechanistic.get(key))
        if values:
            rows.append({"title": title, "description": ", ".join(values[:MAX_TARGET_VALUES]), "meta": meta})

    for key, title in (
        ("kegg_enzymes_a", "Drug A KEGG enzyme hints"),
        ("kegg_enzymes_b", "Drug B KEGG enzyme hints"),
    ):
        values = _enzyme_labels(mechanistic.get(key))
        if values:
            rows.append({"title": title, "description": ", ".join(values[:MAX_TARGET_VALUES]), "meta": "KEGG"})

    rows.extend(_chembl_rows(mechanistic.get("chembl_enrichment")))
    rows.extend(_research_mechanism_rows(research_enrichment or {}))

    if not rows:
        rows.append({"title": "No mechanism rows returned", "description": "No enzyme, target, or pathway rows were available from active sources.", "meta": "Mechanism"})

    pk_detail = pkpd.get("pk_detail") or {}
    overlaps = pk_detail.get("overlaps") or {}
    pd_detail = pkpd.get("pd_detail") or {}
    return {
        "metrics": [
            {"label": "PK overlap", "value": _overlap_metric(overlaps), "tone": "moderate" if any(overlaps.values()) else "neutral"},
            {"label": "PD targets", "value": str(len(pd_detail.get("overlap_targets") or [])), "tone": "neutral"},
            {"label": "Pathways", "value": str(len(mechanistic.get("common_pathways") or [])), "tone": "neutral"},
        ],
        "rows": rows,
    }


def build_sources_list(context: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item in context.get("source_status") or []:
        enabled = bool(item.get("enabled"))
        available = bool(item.get("available"))
        if enabled and available:
            state = "active"
        elif enabled:
            state = "unavailable"
        else:
            state = "disabled"
        rows.append({"name": str(item.get("name") or "Unknown source"), "state": state, "detail": str(item.get("reason") or "")})

    if rows:
        return rows

    sources = context.get("sources") or {}
    for key, values in sources.items():
        rows.append({"name": str(key), "state": "active" if values else "disabled", "detail": ", ".join(_strings(values)) or "No rows"})
    return rows or [{"name": "Source status", "state": "unavailable", "detail": "No source status was returned."}]


def build_references(context: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    drugs = context.get("drugs") or {}
    clinical = ((context.get("signals") or {}).get("clinical_reference") or {})
    research = ((context.get("signals") or {}).get("research_enrichment") or {})
    for side in ("a", "b"):
        drug = drugs.get(side) or {}
        name = str(drug.get("name") or f"Drug {side.upper()}")
        ids = drug.get("ids") or {}
        if ids.get("pubchem_cid"):
            rows.append({"title": f"{name} PubChem compound", "description": f"PubChem CID {ids['pubchem_cid']}", "meta": "PubChem"})
        if ids.get("rxcui"):
            rows.append({"title": f"{name} RxNorm concept", "description": f"RxCUI {ids['rxcui']}", "meta": "RxNorm"})
        if ids.get("drugbank"):
            rows.append({"title": f"{name} DrugBank record", "description": f"DrugBank ID {ids['drugbank']}", "meta": "Licensed if enabled"})

        label = (((clinical.get("openfda_label") or {}).get(side) or {}))
        if label.get("found"):
            sections = label.get("sections") or {}
            section_names = ", ".join(str(key).replace("_", " ") for key in list(sections.keys())[:4])
            rows.append(
                {
                    "title": f"{name} openFDA label",
                    "description": f"Effective time: {label.get('effective_time') or 'unknown'}; sections: {section_names or 'metadata only'}",
                    "meta": "openFDA label",
                }
            )

        dailymed = (((clinical.get("dailymed") or {}).get(side) or {}))
        for record in (dailymed.get("records") or [])[:2]:
            if not isinstance(record, dict):
                continue
            title = record.get("title") or record.get("set_id")
            if title:
                rows.append(
                    {
                        "title": f"{name} DailyMed SPL",
                        "description": str(title),
                        "meta": "DailyMed",
                    }
                )

        fda_ref = (((clinical.get("fda_ddi_reference") or {}).get(side) or {}))
        matches = fda_ref.get("matches") or []
        if matches:
            rows.append(
                {
                    "title": f"{name} FDA CYP/transporter reference",
                    "description": f"{len(matches)} table row(s) matched the drug name in the local FDA DDI reference snapshot.",
                    "meta": "FDA DDI",
                }
            )

    europe_pmc = research.get("europe_pmc") if isinstance(research, dict) else {}
    for article in (europe_pmc or {}).get("articles", [])[:5] if isinstance(europe_pmc, dict) else []:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "Europe PMC article").strip()
        year = str(article.get("year") or "").strip()
        meta = "Europe PMC" + (f" {year}" if year else "")
        rows.append(
            {
                "title": title,
                "description": str(article.get("url") or article.get("doi") or article.get("pmid") or "Literature metadata result"),
                "meta": meta,
            }
        )

    pgx = research.get("fda_pgx") if isinstance(research, dict) else {}
    if isinstance(pgx, dict) and pgx.get("found"):
        for side, label in (("a", "Drug A"), ("b", "Drug B")):
            for match in (pgx.get(side) or [])[:2]:
                if not isinstance(match, dict):
                    continue
                rows.append(
                    {
                        "title": f"{label} FDA PGx page match",
                        "description": str(match.get("snippet") or "FDA PGx page match"),
                        "meta": "FDA PGx",
                    }
                )

    drugcentral = research.get("drugcentral") if isinstance(research, dict) else {}
    if isinstance(drugcentral, dict):
        for side, label in (("a", "Drug A"), ("b", "Drug B")):
            payload = drugcentral.get(side) or {}
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            structure = payload.get("structure") or {}
            title = structure.get("name") or payload.get("drug") or label
            details = []
            if structure.get("cas_reg_no"):
                details.append(f"CAS {structure['cas_reg_no']}")
            if structure.get("formula"):
                details.append(str(structure["formula"]))
            target_count = len(payload.get("targets") or [])
            if target_count:
                details.append(f"{target_count} target/activity row(s)")
            rows.append(
                {
                    "title": f"{title} DrugCentral record",
                    "description": "; ".join(details) or "DrugCentral API record",
                    "meta": "DrugCentral",
                }
            )

    meta = context.get("meta") or {}
    if meta.get("created_at"):
        rows.append({"title": "Evidence context cache", "description": f"Context generated at {meta['created_at']}", "meta": "Cache"})
    rows.append({"title": "OpenFDA caveat", "description": FAERS_CAVEAT, "meta": "FAERS"})
    return rows


def cited_cards_from_context(context: Dict[str, Any]) -> List[str]:
    cards = ["AI assessment"]
    sources = context.get("sources") or {}
    if sources.get("openfda") or ((context.get("signals") or {}).get("faers") or {}):
        cards.append("OpenFDA")
    if sources.get("duckdb") or sources.get("canonical"):
        cards.append("Internal")
    if (context.get("signals") or {}).get("mechanistic"):
        cards.append("Mechanisms")
    return list(dict.fromkeys(cards))


def _research_mechanism_rows(research: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not isinstance(research, dict):
        return rows

    stringdb = research.get("stringdb") or {}
    if isinstance(stringdb, dict):
        mapped = [
            str(row.get("preferred_name") or row.get("query") or "").strip()
            for row in (stringdb.get("mapped") or [])
            if isinstance(row, dict)
        ]
        mapped = [value for value in mapped if value]
        if mapped:
            rows.append(
                {
                    "title": "STRING mapped protein seeds",
                    "description": ", ".join(mapped[:MAX_TARGET_VALUES]),
                    "meta": _returned_note(len(mapped)),
                }
            )
        interactions = []
        for row in (stringdb.get("interactions") or [])[:MAX_TARGET_VALUES]:
            if not isinstance(row, dict):
                continue
            a = row.get("protein_a")
            b = row.get("protein_b")
            if a and b:
                score = row.get("score")
                interactions.append(f"{a}-{b}" + (f" score {score}" if score is not None else ""))
        if interactions:
            rows.append(
                {
                    "title": "STRING protein association context",
                    "description": "; ".join(interactions),
                    "meta": "Hypothesis context",
                }
            )

    drugcentral = research.get("drugcentral") or {}
    if isinstance(drugcentral, dict):
        for side, label in (("a", "Drug A"), ("b", "Drug B")):
            payload = drugcentral.get(side) or {}
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            structure = payload.get("structure") or {}
            targets = []
            for row in (payload.get("targets") or [])[:MAX_TARGET_VALUES]:
                if not isinstance(row, dict):
                    continue
                gene = str(row.get("gene") or "").strip()
                target_name = str(row.get("target_name") or "").strip()
                action = str(row.get("action_type") or row.get("act_type") or "").strip()
                if gene or target_name:
                    target = gene or target_name
                    targets.append(target + (f" ({action})" if action else ""))
            if targets:
                title_name = structure.get("name") or payload.get("drug") or label
                rows.append(
                    {
                        "title": f"DrugCentral target/activity context: {title_name}",
                        "description": "; ".join(targets),
                        "meta": "Mechanism support",
                    }
                )

    open_targets = research.get("open_targets") or {}
    if isinstance(open_targets, dict):
        hits = []
        for side in ("a", "b"):
            payload = open_targets.get(side) or {}
            if not isinstance(payload, dict):
                continue
            for row in (payload.get("hits") or [])[:4]:
                if isinstance(row, dict):
                    name = str(row.get("name") or row.get("id") or "").strip()
                    entity = str(row.get("entity") or "").strip()
                    if name:
                        hits.append(name + (f" ({entity})" if entity else ""))
        if hits:
            rows.append(
                {
                    "title": "Open Targets search context",
                    "description": ", ".join(hits[:MAX_TARGET_VALUES]),
                    "meta": "Biology discovery",
                }
            )

    biogrid = research.get("biogrid") or {}
    if isinstance(biogrid, dict) and biogrid.get("interactions"):
        interactions = []
        for row in (biogrid.get("interactions") or [])[:MAX_TARGET_VALUES]:
            if not isinstance(row, dict):
                continue
            a = row.get("interactor_a")
            b = row.get("interactor_b")
            system = row.get("experimental_system")
            if a and b:
                interactions.append(f"{a}-{b}" + (f" ({system})" if system else ""))
        if interactions:
            rows.append(
                {
                    "title": "BioGRID interaction context",
                    "description": "; ".join(interactions),
                    "meta": "Credentialed source",
                }
            )

    return rows


def _nci_rows(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not value:
        return []
    rows: List[Dict[str, str]] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        score = _display(item.get("score"), "NA")
        panel = str(item.get("panel") or "unknown panel")
        cell = str(item.get("cell_name") or "unknown cell")
        expected = _display(item.get("expected_growth"), "NA")
        observed = _display(item.get("percent_growth"), "NA")
        rows.append(
            {
                "title": f"NCI-ALMANAC cell-line screen: {panel} / {cell}",
                "description": (
                    f"Score {score} = expected growth minus observed growth "
                    f"(expected {expected}, observed {observed}). This is experimental oncology-screen evidence, "
                    "not clinical DDI causality or dosing guidance."
                ),
                "meta": "NCI-ALMANAC",
            }
        )
    if len(value) > 3:
        rows.append(
            {
                "title": "NCI-ALMANAC additional rows",
                "description": f"{len(value) - 3} more matching cell-line assay rows were returned.",
                "meta": _returned_note(len(value)),
            }
        )
    return rows


def _reaction_rows(title: str, rows: List[Tuple[str, int]], *, shown: int = MAX_REACTION_ROWS) -> Tuple[List[Dict[str, str]], int]:
    out: List[Dict[str, str]] = []
    displayed = rows[:shown]
    for term, count in displayed:
        out.append({"title": f"{title}: {term}", "description": f"{count} report(s) in the returned FAERS subset.", "meta": f"n={count}"})
    return out, len(displayed)


def _mechanism_source_note(key: str, mechanistic: Dict[str, Any], count: int) -> str:
    if key in {"targets_a", "targets_b"}:
        side = "a" if key.endswith("_a") else "b"
        if mechanistic.get(f"uniprot_ids_{side}") or mechanistic.get(f"uniprot_targets_{side}"):
            return "UniProt / PubChem"
    if key == "common_pathways" and mechanistic.get("kegg_common_pathways"):
        return "KEGG"
    if key in {"pathways_a", "pathways_b"}:
        side = "a" if key.endswith("_a") else "b"
        sources = []
        if mechanistic.get(f"kegg_pathways_{side}"):
            sources.append("KEGG")
        if mechanistic.get(f"reactome_pathways_{side}"):
            sources.append("Reactome")
        if sources:
            return " / ".join(sources)
    return _returned_note(count)


def _pathway_labels(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            pathway_id = item.get("pathway_id") or item.get("id") or item.get("stId") or item.get("dbId")
            name = item.get("pathway_name") or item.get("name") or item.get("displayName")
            if pathway_id and name:
                out.append(f"{pathway_id}: {name}")
            elif name:
                out.append(str(name))
            elif pathway_id:
                out.append(str(pathway_id))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _protein_labels(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            accession = item.get("uniprot_id") or item.get("primaryAccession") or item.get("accession")
            name = item.get("name") or item.get("protein_name") or item.get("original_id")
            genes = item.get("gene_names") or []
            if isinstance(genes, str):
                genes = [genes]
            gene_text = f" ({', '.join(str(g) for g in genes[:3] if g)})" if genes else ""
            if accession and name:
                out.append(f"{accession}: {name}{gene_text}")
            elif accession:
                out.append(str(accession))
            elif name:
                out.append(str(name))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _enzyme_labels(value: Any) -> List[str]:
    rows = value or []
    if isinstance(rows, dict):
        rows = [rows]
    out: List[str] = []
    for item in rows:
        if isinstance(item, dict):
            enzyme_id = item.get("enzyme_id") or item.get("id")
            name = item.get("enzyme_name") or item.get("name")
            if enzyme_id and name:
                out.append(f"{enzyme_id}: {name}")
            elif name:
                out.append(str(name))
            elif enzyme_id:
                out.append(str(enzyme_id))
        else:
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def _has_chembl_content(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for side in ("a", "b"):
        data = value.get(side)
        if not isinstance(data, dict):
            continue
        validation = data.get("chembl_validation") or {}
        strengths = data.get("enzyme_strength") or {}
        if (
            validation.get("found")
            or validation.get("matches")
            or validation.get("mismatches")
            or any(strengths.get(key) for key in ("strong", "moderate", "weak"))
        ):
            return True
    return False


def _chembl_rows(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, dict):
        return []
    rows: List[Dict[str, str]] = []
    for side, title in (("a", "Drug A ChEMBL bioactivity"), ("b", "Drug B ChEMBL bioactivity")):
        data = value.get(side)
        if not isinstance(data, dict):
            continue
        parts: List[str] = []
        validation = data.get("chembl_validation") or {}
        if validation.get("found"):
            matches = _strings(validation.get("matches"))
            mismatches = _strings(validation.get("mismatches"))
            if matches:
                parts.append("validated enzymes: " + ", ".join(matches[:MAX_ENZYME_PER_ROLE]))
            if mismatches:
                parts.append("additional ChEMBL enzymes: " + ", ".join(mismatches[:MAX_ENZYME_PER_ROLE]))
        strengths = data.get("enzyme_strength") or {}
        for strength in ("strong", "moderate", "weak"):
            values = _strings(strengths.get(strength))
            if values:
                parts.append(f"{strength}: {', '.join(values[:MAX_ENZYME_PER_ROLE])}")
        if parts:
            rows.append({"title": title, "description": "; ".join(parts), "meta": "ChEMBL"})
    return rows


def _pairs(value: Any) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for item in value or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                out.append((str(item[0]), int(item[1])))
            except Exception:
                continue
    return out


def _strings(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [f"{key}: {val}" for key, val in value.items()]
    return [str(item).strip() for item in value if str(item).strip()]


def _display(value: Any, default: str = "Unknown") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().lower()
        if not text or text in {"unknown", "na", "n/a", "none", "null"}:
            return None
        return float(text)
    except Exception:
        return None


def _prr_tone(value: Any) -> str:
    prr = _coerce_float(value)
    if prr is None:
        return "unknown"
    if prr > PRR_HIGH:
        return "high"
    if prr > PRR_MODERATE:
        return "moderate"
    return "low"


def _score_pair(values: Dict[str, Any], a_key: str, b_key: str) -> str:
    return f"A: {_display(values.get(a_key))} / B: {_display(values.get(b_key))}"


def _overlap_metric(overlaps: Dict[str, Any]) -> str:
    parts = []
    for key in ("inhibition", "induction", "shared_substrate"):
        if overlaps.get(key):
            parts.append(key.replace("_", " "))
    return ", ".join(parts) if parts else "Not detected"


def _shown_of_total(shown: int, total: int) -> str:
    if total > shown:
        return f"top {shown} of {total}"
    return str(total)


def _subset_note(*sections: Tuple[int, int, str]) -> str:
    truncated = [
        f"top {shown} of {total} for {label}"
        for shown, total, label in sections
        if total > shown
    ]
    if truncated:
        return "Showing " + "; ".join(truncated) + "."
    return "Showing all rows returned by retrieval."


def _returned_note(count: int) -> str:
    return f"{count} returned" if count else "No rows"
