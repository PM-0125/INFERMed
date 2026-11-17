"""Explicit patient-condition checks. Unknown conditions never mean normal.

This bounded checker reports its coverage; matching recognized conditions is not
a determination of clinical appropriateness or a substitute for prescribing review.
"""

import math
import re

LIMITATION = "Recognized conditions only; not clinical validation. Dose, route, indication, timing and unparsed qualifications may still affect applicability."


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def assess_applicability(text, patient_context=None, kind="descriptive_claim"):
    patient = patient_context or {}
    if hasattr(patient, "to_dict"):
        patient = patient.to_dict()
    patient = {**(patient.get("raw") or {}), **patient}
    lower = text.lower()
    result = dict(
        status="not_established",
        required_fields=[],
        checked_conditions=[],
        population_differences=[],
        reasons=[],
        limitation=LIMITATION,
    )
    if kind == "study_observation":
        result["status"] = "study_population_only"
        result["reasons"].append(
            "Reported study regimen and population are not a patient-specific prescription."
        )
        population = re.search(
            r"\b(?:to|in|among)\s+\d+\s+((?:(?:healthy|normal|adult|young|male|female)\s+)*)(?:volunteers|subjects|participants)\b",
            lower,
        )
        if population:
            prefix = lower[: population.start()].strip()
            index_construction = not prefix or re.search(
                r"\b(?:was|were)\s+(?:administered|given|conducted|evaluated)\s*$",
                prefix,
            )
            if not index_construction or re.search(
                r"\b(?:than|compared|versus|controls?|placebo)\b", prefix
            ):
                population = None  # Unknown relationships, including comparators, remain unclassified.
        if population and re.search(r"\b(?:healthy|normal)\b", population[1]):
            for field in ("renal_function", "hepatic_function"):
                if patient.get(field) not in (None, "unknown", "normal"):
                    result["population_differences"].append(field)
        if (
            population
            and re.search(r"\bmale\b", population[1])
            and patient.get("sex") == "female"
        ):
            result["population_differences"].append("sex")
        # Only the explicit canonical index-cohort age, never a comparator's age.
        age = re.match(
            r"^the .+ pharmacokinetic study in \d+ subjects aged (\d+) years or older reported\b",
            lower,
        )
        if age:
            actual = _number(patient.get("age_years"))
            state = (
                "unknown"
                if actual is None
                else "matched" if actual >= int(age[1]) else "conflict"
            )
            result["checked_conditions"].append(
                {
                    "field": "age_years",
                    "expected": ">= " + age[1],
                    "actual": actual,
                    "state": state,
                }
            )
            if state == "unknown":
                result["required_fields"].append("age_years")
            elif state == "conflict":
                result["population_differences"].append("age_years")
            result["reasons"].append(
                "Study age comparison only; matching age does not establish renal status, exposure, or a patient-specific regimen."
            )
        return result

    def check(field, expected, actual, matches):
        unknown = actual is None or actual == "unknown"
        state = "unknown" if unknown else "matched" if matches else "conflict"
        result["checked_conditions"].append(
            dict(field=field, expected=expected, actual=actual, state=state)
        )
        if unknown:
            result["required_fields"].append(field)

    # Only recognizable applicability constructions, not every disease mention.
    conditional = bool(
        re.search(
            r"\b(?:if|when|only|in patients|for patients|patients with|in pregnant|for pregnant)\b",
            lower,
        )
    )
    complex_condition = bool(
        re.search(
            r"\b(?:not|no|non|without|unless|except|or|absent|absence|neither|resolved)\b",
            lower,
        )
    )
    if conditional and not complex_condition:
        for organ, field in [
            ("renal", "renal_function"),
            ("hepatic", "hepatic_function"),
        ]:
            for match in re.finditer(
                r"\b(mild|moderate|severe) " + organ + r" impairment\b", lower
            ):
                expected = match[1] + "_impairment"
                actual = patient.get(field)
                check(field, expected, actual, actual == expected)
        if re.search(r"\bpregnant\b", lower):
            expected = (
                "not_pregnant" if re.search(r"\bnot pregnant\b", lower) else "pregnant"
            )
            actual = patient.get("pregnancy_status")
            check("pregnancy_status", expected, actual, actual == expected)
        for threshold in re.finditer(
            r"\b(?:crcl|creatinine clearance)\s*(<=|>=|<|>|≤|≥)\s*(\d+(?:\.\d+)?)\s*ml/min(?!/)",
            lower,
        ):
            labs = [
                lab
                for lab in patient.get("labs", [])
                if str(lab.get("name", "")).lower() in {"crcl", "creatinine clearance"}
                and str(lab.get("unit", "")).lower().replace(" ", "") == "ml/min"
            ]
            # Multiple observations need explicit temporal selection, never first-row guessing.
            value = _number(labs[0].get("value")) if len(labs) == 1 else None
            limit = float(threshold[2])
            op = threshold[1]
            comparison = (
                value is not None
                and {
                    "<": value < limit,
                    ">": value > limit,
                    "<=": value <= limit,
                    ">=": value >= limit,
                    "≤": value <= limit,
                    "≥": value >= limit,
                }[op]
            )
            check(
                "creatinine_clearance_ml_min",
                threshold[1] + " " + threshold[2],
                value,
                comparison,
            )
        for age in re.finditer(
            r"\b(?:age|aged)\s*(?:under|below|over|above)\s*(\d+)\b", lower
        ):
            actual = _number(patient.get("age_years"))
            is_lower = bool(re.search(r"under|below", age[0]))
            check(
                "age_years",
                age[0],
                actual,
                actual is not None
                and (actual < int(age[1]) if is_lower else actual > int(age[1])),
            )
        if re.search(r"\bdialysis\b", lower):
            actual = patient.get("renal_function")
            check("renal_function", "dialysis", actual, actual == "dialysis")

    inr_interpretation = bool(
        re.search(
            r"(?:\b(?:current|patient[’\x27]s|this patient|the)\b.{0,35}\binr\b|\binr\s*(?=\d|is\b)).{0,100}\b(?:therapeutic|normal|elevated|high|low|supratherapeutic|subtherapeutic)\b",
            lower,
        )
    )
    if inr_interpretation:
        result["patient_interpretation"] = "inr"
        result["inr_interpretation_supported"] = False
        current = _number(patient.get("current_inr"))
        target = patient.get("inr_target_range")
        valid_target = (
            isinstance(target, (list, tuple))
            and len(target) == 2
            and all(_number(n) is not None for n in target)
            and float(target[0]) <= float(target[1])
        )
        if current is None:
            result["required_fields"].append("current_inr")
        if not valid_target:
            result["required_fields"].append("inr_target_range")
        if not patient.get("indication"):
            result["required_fields"].append("indication")
        if current is not None and valid_target and patient.get("indication"):
            # Only explicit relational wording is machine-checkable. Near-limit,
            # normal, negated and mixed descriptions need clinical interpretation.
            relation = re.search(
                r"\binr\b[^.!?]{0,40}\bis\s+(therapeutic|supratherapeutic|subtherapeutic)\s*[.]?$",
                lower,
            )
            if relation:
                expected = relation[1]
                lo, hi = map(float, target)
                matches = {
                    "therapeutic": lo <= current <= hi,
                    "supratherapeutic": current > hi,
                    "subtherapeutic": current < lo,
                }[expected]
                stated = re.search(r"\binr\s*[:=]?\s*(\d+(?:\.\d+)?)", lower)
                if stated and float(stated[1]) != current:
                    matches = False
                    result["reasons"].append(
                        "The INR value stated in the claim differs from the supplied patient value."
                    )
                result["inr_interpretation_supported"] = matches
                check("current_inr", expected, current, matches)
        result["reasons"].append(
            "Interpreting this INR requires the supplied treatment indication and patient-specific target; no default range is assumed."
        )
    states = [c["state"] for c in result["checked_conditions"]]
    if "conflict" in states:
        result["status"] = "condition_conflict"
        result["reasons"].append(
            "A recognized source condition differs from supplied patient data."
        )
    elif result["required_fields"]:
        result["status"] = "missing_context"
        result["reasons"].append(
            "Required information for a recognized applicability condition was not supplied."
        )
    elif states and not (
        inr_interpretation and not result["inr_interpretation_supported"]
    ):
        result["status"] = "conditions_matched"
        result["reasons"].append(
            "The explicitly checked conditions match supplied patient data; other qualifications are not established."
        )
    else:
        result["reasons"].append(
            "No supported automatic patient-applicability rule covers this statement."
        )
    result["required_fields"] = list(dict.fromkeys(result["required_fields"]))
    return result


def patient_causal_interpretation(text, patient_context):
    """Screen explicit patient extrapolations, not all pharmacology statements."""
    if not patient_context:
        return False
    if hasattr(patient_context, "to_dict"):
        patient_context = patient_context.to_dict()
    patient_anchor = re.search(
        r"\b(?:this patient|the patient|patient.s|patient[- ]specific|age\s*\d+|\d+[-‑ ]year[-‑ ]old|patient modifiers)\b|^(?:Age|Severe renal impairment|Moderate renal impairment)\s*:",
        text,
        re.I,
    )
    if not patient_anchor and patient_context.get("renal_function") not in {
        None,
        "unknown",
        "normal",
    }:
        patient_anchor = re.search(r"\brenal impairment\b", text, re.I)
    effect = re.search(
        r"\b(?:increas\w*|reduc\w*|elevat\w*|intensif\w*|amplif\w*|heighten\w*|affect\w*|magnif\w*|higher|associated|clearance|sensitivity)\b",
        text,
        re.I,
    )
    return bool(patient_anchor and effect)
