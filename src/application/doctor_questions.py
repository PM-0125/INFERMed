"""Question plans use source records and applicability results, never invented effects."""

import re
from src.domain.explanation.claim_types import DIRECTIVE_PATTERN

TOPICS = {
    "renal": (
        "renal function",
        r"\b(?:renal|kidney\w*|creatinine|crcl|e?gfr|dialysis)\b",
        "renal_function",
    ),
    "hepatic": (
        "hepatic function",
        r"\b(?:hepatic|liver|cirrhosis)\b",
        "hepatic_function",
    ),
    "qt": ("QT risk", r"\b(?:qtc?|torsade\w*|arrhythm\w*)\b", "qt_risk"),
}
TOPIC_FIELDS = {
    "renal": {"renal_function", "creatinine_clearance_ml_min", "egfr", "dialysis"},
    "hepatic": {"hepatic_function"},
    "qt": {"qt_risk"},
}


def plan_question(question, records):
    topics = [
        key
        for key, (_, pattern, _) in TOPICS.items()
        if re.search(pattern, question or "", re.I)
    ]
    relevant = []
    for record in records:
        applicability = record["patient_applicability"]
        if any(
            re.search(TOPICS[t][1], record["text"], re.I)
            or TOPICS[t][2] in applicability.get("population_differences", [])
            or any(
                c.get("field") in TOPIC_FIELDS[t]
                for c in applicability.get("checked_conditions", [])
            )
            for t in topics
        ):
            relevant.append(record["record_id"])
    return {
        "topic": topics[0] if len(topics) == 1 else None,
        "topics": topics,
        "relevant_record_ids": relevant,
        "status": "recognized" if topics else "unsupported_question",
        "limitation": "Topic routing is not proof of applicability or clinical entailment.",
    }


def question_intro(plan, records, patient=None):
    lines = ["## Direct Answer"]
    if not plan["topics"]:
        return lines + [
            "This question is outside the supported source-to-patient question plans; no clinical answer has been established."
        ]
    for topic in plan["topics"]:
        name, pattern, field = TOPICS[topic]
        direct = [
            r
            for r in records
            if r["publication"] == "accepted" and re.search(pattern, r["text"], re.I)
        ]
        if direct:
            lines.append(
                f"The source observations below address {name}; patient-specific suitability remains unestablished."
            )
            if any(r.get("kind") == "structured_study_observation" for r in direct):
                lines.append(
                    "The reported measurement belongs to the cited study population; it is not a measurement supplied for this patient."
                )
                age_checks = [
                    c
                    for r in direct
                    if r.get("kind") == "structured_study_observation"
                    for c in r["patient_applicability"].get("checked_conditions", [])
                    if c["field"] == "age_years"
                ]
                if age_checks and all(c["state"] == "matched" for c in age_checks):
                    lines.append(
                        "The supplied age meets the reported minimum study age. This age match does not establish renal-function applicability or a patient-specific effect."
                    )
                elif any(c["state"] == "conflict" for c in age_checks):
                    lines.append(
                        "The supplied age is below a reported minimum study age; that study does not establish an effect for this patient."
                    )
        else:
            lines.append(
                f"No accepted retrieved record in this review directly addresses {name}. This does not establish a gap in the published literature or absence of a {topic} effect."
            )
        differences = [
            r
            for r in records
            if r["publication"] == "accepted"
            and field in r["patient_applicability"].get("population_differences", [])
        ]
        if differences:
            lines.append(
                f"The supplied {name} differs from the reported study population. The quoted study does not establish an effect size for this patient."
            )
    if "renal" in plan["topics"] and patient is not None:
        fields = list(patient) + list((patient.get("raw") or {}))
        fields += [
            str(lab.get("name", ""))
            for lab in patient.get("labs", [])
            if isinstance(lab, dict)
        ]
        if not any(
            re.search(r"creatinine|crcl|e?gfr", field, re.I) for field in fields
        ):
            lines.append(
                "No creatinine-clearance or eGFR field was supplied in the structured patient context."
            )
    withheld = [r for r in records if r["publication"] == "withheld"]
    if withheld:
        lines += [
            "",
            "## Retrieved Material Requiring Review",
            "Relevant source material was retrieved but did not pass the publication gate. Its clinical applicability remains unestablished.",
        ]
        for record in withheld:
            # Section labels are navigation metadata, not another instruction.
            # Keep the exact section in the audit; use neutral wording in prose.
            section = {
                "geriatric_use": "older adults",
                "pediatric_use": "children",
            }.get(record["section"], record["section"].replace("_", " "))
            if re.search(DIRECTIVE_PATTERN, section, re.I):
                section = "label context"
            lines.append(
                f'{record["source_name"]}: {section} ({", ".join(record["drug_scope"])}).'
            )
        if any(
            r.get("verification", {}).get("prescribing_authority") == "not_established"
            for r in withheld
        ):
            lines.append(
                "Source authority for a patient-specific action: not established for the listed records."
            )
    return lines
