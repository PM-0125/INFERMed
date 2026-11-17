"""A deliberately narrow source grammar for reported elderly-study renal data.

This extracts a study measurement, never a patient measurement or dose rule.
Only complete retained source units with one cohort and one endpoint qualify.
"""

import re
from src.domain.explanation.passages import source_passages


def renal_study_fields(card):
    if (
        card.claim_type != "label_context"
        or card.evidence_grade != "label"
        or len(card.drug_scope) != 1
        or card.source_type
        in {"mechanism_engine", "reasoning_engine", "normalized_context"}
    ):
        return []
    drug = card.drug_scope[0]
    complete = {
        (p.get("section"), p.get("text"))
        for p in card.payload.get("passages", [])
        if p.get("section_complete") is True
    }
    facts = []
    for passage in source_passages(card):
        if (
            passage["text"] != passage["context"]
            or (passage["section"], passage["text"]) not in complete
        ):
            continue
        text = re.sub(r"\s+", " ", passage["text"]).strip()
        # Full-unit matching is intentional. Unknown intervening cohorts,
        # thresholds, qualifications or corrections must not disappear while
        # projecting a measurement. This supports one explicit label grammar,
        # not arbitrary study prose or clinical inference.
        number = r"\d+(?:\.\d+)?"
        name = re.escape(drug)
        header = (
            r"A pharmacokinetic study was conducted in (?P<count>\d+) subjects, (?P<age>\d+) years of age or older "
            r"receiving a single (?P<dose>"
            + number
            + r") mg oral dose of "
            + name
            + r"\."
        )
        intermediate = (
            r"Ten of these patients were concomitantly receiving diuretics\. "
            r"The C maxwas "
            + number
            + r" mcg/mL and occurred at "
            + number
            + r" hours post dose\. "
            r"The mean AUC was "
            + number
            + r" ± "
            + number
            + r" mcg•h/mL, and the mean terminal half-life was "
            + number
            + r" hours\. "
            r"These pharmacokinetic parameter values are higher than analogous values reported for normal young male volunteers\. "
            r"Coadministration of diuretics did not significantly alter the AUC or C max\. "
        )
        endpoint = (
            r"In addition, creatinine clearance \((?P<value>" + number + r") mL/min\), "
            r"the percent of drug recovered unchanged in urine \((?:0 to 24 hours, )?"
            + number
            + r"%\), "
            r"and the "
            + name
            + r" renal clearance estimates \("
            + number
            + r" mL/min/kg\) "
            r"for the elderly were generally lower than those of younger volunteers\."
        )
        trailing = (
            r" Thus, the alteration of "
            + name
            + r" disposition in the elderly appears to be related to reduced renal function characteristic of this group\. "
            r"A plot of each subject[’']s terminal elimination half-life versus creatinine clearance compared to the predicted half-life - creatinine clearance curve derived from normal subjects and subjects with varying degrees of renal insufficiency indicated that \d+ of (?P=count) subjects fell within the 95% confidence limit of the predicted half-life-creatinine clearance curves\. "
            r"These results are consistent with the hypothesis that higher values for the pharmacokinetic parameters observed in the elderly subjects compared to normal young male volunteers are due to the decreased kidney function that is expected in the elderly\."
        )
        match = re.fullmatch(
            r"(?:CLINICAL PHARMACOLOGY Pharmacokinetics in Elderly )?"
            + header
            + r" (?:"
            + intermediate
            + r")?"
            + endpoint
            + r"(?:"
            + trailing
            + r")?",
            text,
        )
        if match is None:
            continue
        count, age, dose, value = (
            match.group(key) for key in ("count", "age", "dose", "value")
        )
        claim = f"The {drug} pharmacokinetic study in {count} subjects aged {age} years or older reported creatinine clearance of {value} mL/min, generally lower than in younger volunteers."
        facts.append(
            {
                "text": claim,
                "section": passage["section"],
                "source_context": passage["context"],
                "population_n": count,
                "minimum_age_years": age,
                "study_dose_mg": dose,
                "value": value,
                "unit": "mL/min",
                "field": "creatinine_clearance",
            }
        )
    return facts
