"""Bind model-selected anchors to immutable retrieved passages, not model quotes.

Selection is a transport contract only. Publication and patient checks still run
on the rendered wording; selecting a passage never certifies clinical support.
"""

import hashlib
import json
import re

from src.domain.evidence.entities import EvidenceCard
from src.domain.explanation.passages import source_passages
from src.domain.explanation.claim_types import claim_kind, DIRECTIVE_PATTERN
from src.domain.explanation.applicability import assess_applicability
from src.domain.explanation.action_support import INTERPRETATION_WITHHELD


def passage_catalog(graph):
    drugs = (graph.get("decision") or {}).get("drugs") or []
    entries = []
    for raw in graph.get("evidence") or []:
        if raw.get("claim_type") != "label_context":
            continue
        card = EvidenceCard(**raw)
        if (
            card.claim_type != "label_context"
            or not card.drug_scope
            or not set(card.drug_scope).issubset(drugs)
            or card.source_type
            in {"reasoning_engine", "mechanism_engine", "normalized_context"}
        ):
            continue
        eligible = {p["text"] for p in source_passages(card, actions=True)}
        selected = card.payload.get("passages")
        complete_boundaries = (
            {
                (p.get("section"), p.get("text"))
                for p in selected
                if p.get("section_complete") is True
            }
            if isinstance(selected, list)
            else None
        )
        candidates = {}
        for p in source_passages(card):
            text = p["text"]
            if text != p["context"]:
                continue  # A sentence match does not preserve its clinical context.
            if (
                complete_boundaries is not None
                and (p["section"], text) not in complete_boundaries
            ):
                continue  # Retrieval windows can begin after a clinical condition.
            # No truncation or ellipsis: omit over-budget candidates entirely.
            if not 20 <= len(text) <= 1800 or any(
                c in text for c in ['"', "“", "”", "{{", "}}"]
            ):
                continue
            partner = any(
                d not in card.drug_scope
                and re.search(r"(?<!\w)" + re.escape(d) + r"(?!\w)", text, re.I)
                for d in drugs
            )
            if (
                p["section"].split("::")[0]
                in {"drug_interactions", "clinical_pharmacology", "precautions"}
                and not partner
            ):
                continue
            candidates[text] = (p, partner)
        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                item[1],
                bool(
                    re.search(
                        r"monitor|renal|clearance|bleeding|half.life|CYP",
                        item[0]["text"],
                        re.I,
                    )
                ),
                -len(item[0]["text"]),
            ),
            reverse=True,
        )
        for p, _ in ordered[:12]:
            kind = claim_kind(
                p["text"], bool(re.search(DIRECTIVE_PATTERN, p["text"], re.I))
            )
            study_context = bool(
                re.search(r"\b(?:volunteers|subjects|participants)\b", p["text"], re.I)
                or (
                    re.search(
                        r"\b\d+\s+(?:(?:healthy|normal|adult|male|female|young|elderly)\s+)*patients\b",
                        p["text"],
                        re.I,
                    )
                    and re.search(
                        r"\b(?:administered|received|given|study|studies|trial)\b",
                        p["text"],
                        re.I,
                    )
                )
            )
            if study_context:
                kind = "study_observation"
            identity = json.dumps(
                [card.evidence_id, card.drug_scope, p["section"], p["text"]],
                ensure_ascii=False,
            )
            identifier = "sp_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            entries.append(
                {
                    "passage_id": identifier,
                    "selection": "{{source:" + identifier + "}}",
                    "evidence_id": card.evidence_id,
                    "source_name": card.source_name,
                    "drug_scope": card.drug_scope,
                    "section": p["section"],
                    "text": p["text"],
                    "action_support_eligible": p["text"] in eligible
                    and not study_context,
                    "claim_kind": kind,
                    "patient_applicability": assess_applicability(
                        p["text"], graph.get("patient_context"), kind
                    ),
                    "content_id": "src_"
                    + hashlib.sha256(
                        json.dumps(
                            [
                                card.source_name,
                                card.payload.get("set_id"),
                                card.payload.get("spl_version")
                                or card.payload.get("version"),
                                card.drug_scope,
                                p["section"],
                                p["text"],
                            ],
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()[:24],
                }
            )
    return entries


def selection_instructions():
    return (
        "\nDoctor source anchors: select relevant entries from source_passage_catalog by writing their "
        "selection token, e.g. {{source:sp_...}}, ALONE on a line. "
        "For NON-LABEL evidence, retain ordinary [ev_...] citations; NEVER put an ev_ ID inside "
        "a source token. Only sp_ passage IDs are valid selections. The backend inserts the complete "
        "verbatim passage, bound drug and citation. Never type, shorten, paraphrase or re-cite label "
        "quotations yourself. Label evidence IDs must only be introduced by selection tokens, "
        "never attached to your own prose. Do not add a citation or instruction on the token line. Use distinct "
        "passages for mechanism, clinical concern and monitoring when available. Prefer concise "
        "relevant passages; do not repeat the same passage. Only action_support_eligible=true passages "
        "may be selected for Monitoring & Actions, and only when their conditions apply. "
        "Study observations belong in the evidence explanation, never as prescribing advice. "
        "Explain patient implications separately; a selection does not validate your interpretation. "
        "Do not invent a dosing schedule or translate a study endpoint to a different endpoint.\n"
    )


def render_selections(text, catalog, label_evidence_ids=(), *, strict=True):
    by_id = {entry["passage_id"]: entry for entry in catalog}
    label_ids = set(label_evidence_ids) | {entry["evidence_id"] for entry in catalog}
    output, audit = [], []

    def reject(line, reason):
        if strict:
            raise ValueError(reason)
        output.append(INTERPRETATION_WITHHELD)
        audit.append({"status": "rejected", "text": line, "reason": reason})

    in_actions = False
    for line in text.splitlines():
        heading = re.match(r"^\s*(#{1,6})\s+(.+)", line)
        if heading:
            action_heading = bool(
                re.search(
                    r"\b(?:monitor(?:ing)?|actions?|management|recommendations?|dose|dosing)\b",
                    heading[2],
                    re.I,
                )
            )
            in_actions = action_heading or (in_actions and len(heading[1]) > 2)
        if label_ids.intersection(re.findall(r"\bev_[A-Za-z0-9_]+\b", line)):
            reject(
                line,
                "Use source selection tokens for label evidence, not manually written label citations",
            )
            continue
        if "{{source" not in line.casefold():
            if re.search(r"\bsp_[A-Za-z0-9_]+\b", line):
                reject(
                    line,
                    "Passage IDs are selections, not citations for model prose; use the exact token alone",
                )
                continue
            output.append(line)
            continue
        match = re.fullmatch(r"\s*(?:[-*] )?\{\{source:(sp_[a-f0-9]{20})\}\}\s*", line)
        if not match or match[1] not in by_id:
            reject(
                line,
                "Invalid source selection; use an exact catalog token alone on its line",
            )
            continue
        entry = by_id[match[1]]
        if in_actions and (
            not entry["action_support_eligible"]
            or entry["claim_kind"] != "prescribing_advice"
        ):
            reject(
                line,
                "Selected passage cannot be used in Monitoring & Actions; use an eligible non-study passage or state the limitation",
            )
            continue
        display = re.sub(r"\s+", " ", entry["text"]).strip()
        output.append(
            f'Source guidance ({", ".join(entry["drug_scope"])}): "{display}" [{entry["evidence_id"]}]'
        )
        audit.append({**entry, "status": "resolved"})
    return "\n".join(output), audit
