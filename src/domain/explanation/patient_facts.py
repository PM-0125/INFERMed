"""Literal patient-input presentation, with no clinical interpretation."""

import math


def patient_fact_lines(patient):
    patient = patient or {}
    rows = []
    for field, label in [("age_years", "age in years"), ("current_inr", "INR")]:
        value = patient.get(field)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            suffix = (
                " (interpretation not established)" if field == "current_inr" else ""
            )
            rows.append(
                {
                    "text": f"Supplied {label}: {value:g}{suffix}.",
                    "field": field,
                    "value": value,
                }
            )
    for field, label, allowed in [
        (
            "renal_function",
            "renal function",
            {
                "normal",
                "mild_impairment",
                "moderate_impairment",
                "severe_impairment",
                "dialysis",
            },
        ),
        (
            "hepatic_function",
            "hepatic function",
            {"normal", "mild_impairment", "moderate_impairment", "severe_impairment"},
        ),
        ("qt_risk", "QT history", {"known_long_qt", "possible_qt_risk", "low_risk"}),
    ]:
        value = patient.get(field)
        if isinstance(value, str) and value in allowed:
            text = f'Supplied {label}: {value.replace("_", " ")}.'
        else:
            value = None
            text = f"{label[0].upper() + label[1:]}: not supplied."
        rows.append({"text": text, "field": field, "value": value})
    return rows
