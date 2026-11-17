"""Resolve audit references to honest source labels at the presentation boundary."""

import re
from copy import deepcopy

from src.domain.explanation.action_support import REFERENCE


def source_label(card):
    scope = ", ".join(card.drug_scope)
    name = str(card.source_name or "Retrieved source")
    if card.claim_type == "label_context":
        name = f"{scope or 'Drug'} prescribing information ({name})"
    elif scope:
        name = f"{name} — {scope}"
    # Metadata is data, never Markdown markup or a model-provided link.
    return re.sub(r"[\[\]<>`*\r\n]", "", name)


def render_citations(text, cards, audience):
    if (audience or "doctor").lower() not in {"doctor", "clinician", "patient", "pt"}:
        return text
    labels = {card.evidence_id: source_label(card) for card in cards}

    def replace_group(match):
        names = list(
            dict.fromkeys(
                labels.get(ref, "Source unavailable")
                for ref in REFERENCE.findall(match[0])
            )
        )
        return "(" + "; ".join(names) + ")"

    text = re.sub(r"[\[【][^\]】\n]*\bev_[^\]】\n]*[\]】]", replace_group, text)
    text = REFERENCE.sub(lambda match: labels.get(match[0], "Source unavailable"), text)
    return (
        format_doctor_note(text)
        if (audience or "doctor").lower() in {"doctor", "clinician"}
        else text
    )


def format_doctor_note(text):
    """Format known note sections after checks; never rewrite clinical statements."""
    list_sections = {
        "Patient Context",
        "Interaction Mechanism",
        "Clinical Concern",
        "Monitoring & Actions",
        "Evidence Limitations",
        "Retrieved Material Requiring Review",
        "Direct Answer",
    }
    lines, section = [], ""
    for line in text.splitlines():
        heading = re.fullmatch(r"## (.+)", line)
        if heading:
            section = heading[1]
            lines.extend(
                [
                    "## "
                    + ("Clinical Summary" if section == "Bottom Line" else section),
                    "",
                ]
            )
            continue
        if (
            line
            and section in list_sections
            and not re.match(r"^\s*(?:[-*+] |\d+\. |[|>#`])", line)
        ):
            if section == "Patient Context" and ":" in line:
                label, value = line.split(":", 1)
                line = "**" + label + ":**" + value
            line = "- " + line
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def present_verification(verification, cards, audience):
    result = deepcopy(verification)
    labels = {card.evidence_id: source_label(card) for card in cards}
    for claim in result.get("claims", []):
        claim["text"] = render_citations(claim["text"], cards, audience)
        for match in claim.get("source_matches", []):
            match["source_name"] = labels.get(
                match.pop("evidence_id", ""), match.get("source_name", "Source")
            )
        refs = claim.get("evidence_ids", [])
        claim["source_labels"] = list(
            dict.fromkeys(labels.get(ref, "Source unavailable") for ref in refs)
        )
        if (audience or "doctor").lower() in {"doctor", "clinician", "patient", "pt"}:
            claim["evidence_ids"] = []
    # Raw reviewer quotes can include rejected recommendations. Keep them only
    # in persisted verification, not in the clinical response.
    review = result.get("audience_review")
    if review:
        result["audience_review"] = {
            key: review.get(key) for key in ("status", "attempts", "error_type")
        }
    return result
