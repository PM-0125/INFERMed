from __future__ import annotations

import re

from src.domain.medication.entities import (
    DoseRouteForm,
    Ingredient,
    MedicationConcept,
    NormalizationCandidate,
    concept_id_for,
)


_SPACE_RE = re.compile(r"\s+")
_STRENGTH_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b", re.IGNORECASE)
_SALT_RE = re.compile(
    r"\b(?:sodium|potassium|calcium|magnesium|hydrochloride|hcl|phosphate|sulfate|sulphate|mesylate|besylate|succinate|tartrate|maleate|fumarate)\b",
    re.IGNORECASE,
)
_FORM_RE = re.compile(
    r"\b(?:tablet|tablets|tab|tabs|capsule|capsules|cap|caps|solution|suspension|injection|injectable|cream|ointment|gel|patch|extended release|delayed release|xr|er|dr)\b",
    re.IGNORECASE,
)
_COMBO_SPLIT_RE = re.compile(r"\s*(?:/|\+|,|;|\band\b|\bwith\b)\s*", re.IGNORECASE)

_ALIASES = {
    "coumadin": "warfarin",
    "diflucan": "fluconazole",
    "advil": "ibuprofen",
    "motrin": "ibuprofen",
    "eliquis": "apixaban",
    "xarelto": "rivaroxaban",
    "plavix": "clopidogrel",
    "zocor": "simvastatin",
    "biaxin": "clarithromycin",
    "prilosec": "omeprazole",
    "tylenol": "acetaminophen",
    "paracetamol": "acetaminophen",
    "asa": "aspirin",
}

_AMBIGUOUS = {
    "asa": [
        NormalizationCandidate("aspirin", ["aspirin"], "alias_match", provenance=["local_alias:asa"]),
        NormalizationCandidate("aminosalicylic acid", ["aminosalicylic acid"], "ambiguous", provenance=["local_ambiguity:asa"]),
    ],
}

_IDENTIFIERS = {
    "warfarin": {"pubchem_cid": "54678486", "rxnorm": "11289"},
    "fluconazole": {"pubchem_cid": "3365", "rxnorm": "4450"},
    "ibuprofen": {"pubchem_cid": "3672", "rxnorm": "5640"},
    "aspirin": {"pubchem_cid": "2244", "rxnorm": "1191"},
    "apixaban": {"pubchem_cid": "10182969", "rxnorm": "1364430"},
    "rivaroxaban": {"pubchem_cid": "9875401", "rxnorm": "1114195"},
    "clopidogrel": {"pubchem_cid": "60606", "rxnorm": "32968"},
    "simvastatin": {"pubchem_cid": "54454", "rxnorm": "36567"},
    "clarithromycin": {"pubchem_cid": "84029", "rxnorm": "21212"},
    "omeprazole": {"pubchem_cid": "4594", "rxnorm": "7646"},
    "acetaminophen": {"pubchem_cid": "1983", "rxnorm": "161"},
}


def normalize_medication_text(raw_text: str) -> str:
    cleaned = _SPACE_RE.sub(" ", str(raw_text or "").strip())
    return cleaned.lower()


def normalize_medication(raw_text: str) -> MedicationConcept:
    raw = str(raw_text or "").strip()
    text = normalize_medication_text(raw)
    strength = _extract_strength(raw)
    text_no_strength = _STRENGTH_RE.sub("", text)
    text_no_strength = _strip_form_terms(text_no_strength)
    text_no_strength = _SPACE_RE.sub(" ", text_no_strength).strip()
    dose_route_form = DoseRouteForm(dose=strength) if strength else None
    stripped = _strip_salt_terms(text_no_strength)
    parts = [part for part in _COMBO_SPLIT_RE.split(stripped) if part]
    if not parts and stripped:
        parts = [stripped]

    provenance: list[str] = ["text_normalized"]
    aliases: list[str] = []
    candidates: list[NormalizationCandidate] = []
    ingredients: list[str] = []
    confidence = "normalized_text"

    for part in parts:
        resolved = _ALIASES.get(part, part)
        if resolved != part:
            provenance.append(f"local_alias:{part}->{resolved}")
            aliases.append(part)
            confidence = "alias_match"
        if part in _AMBIGUOUS:
            candidates.extend(_AMBIGUOUS[part])
            confidence = "ambiguous"
        ingredients.append(resolved)

    ingredients = list(dict.fromkeys(item for item in ingredients if item))
    if len(ingredients) > 1 and confidence != "ambiguous":
        confidence = "combination_product"
        provenance.append("combination_product_split")
    elif text == raw.lower() and confidence == "normalized_text":
        confidence = "exact_text"
    if not ingredients:
        confidence = "unresolved"

    normalized = " + ".join(ingredients)
    identifiers = _merged_identifiers(ingredients)
    ingredient_details = [
        Ingredient(name=ingredient, identifiers=_IDENTIFIERS.get(ingredient, {}))
        for ingredient in ingredients
    ]
    if not candidates and ingredients:
        candidates = [
            NormalizationCandidate(
                normalized_name=normalized,
                ingredients=ingredients,
                confidence=confidence,
                identifiers=identifiers,
                provenance=provenance,
            )
        ]

    payload = {"normalized_name": normalized, "ingredients": ingredients, "identifiers": identifiers}
    return MedicationConcept(
        concept_id=concept_id_for(payload),
        raw_text=raw,
        normalized_name=normalized,
        ingredients=ingredients,
        identifiers=identifiers,
        ingredient_details=ingredient_details,
        dose_route_form=dose_route_form,
        aliases=aliases,
        candidates=candidates,
        provenance=provenance,
        ambiguity_reason=_ambiguity_reason(candidates) if confidence == "ambiguous" else None,
        confidence=confidence,
    )


def medication_set_key(concepts: list[MedicationConcept]) -> str:
    ids = sorted(concept.concept_id for concept in concepts)
    return "medset:" + ":".join(ids)


def _extract_strength(raw: str) -> str | None:
    match = _STRENGTH_RE.search(raw or "")
    return match.group(0) if match else None


def _strip_salt_terms(text: str) -> str:
    stripped = _SALT_RE.sub("", text)
    return _SPACE_RE.sub(" ", stripped).strip()


def _strip_form_terms(text: str) -> str:
    stripped = _FORM_RE.sub("", text)
    return _SPACE_RE.sub(" ", stripped).strip()


def _merged_identifiers(ingredients: list[str]) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for ingredient in ingredients:
        for key, value in _IDENTIFIERS.get(ingredient, {}).items():
            if len(ingredients) == 1:
                identifiers[key] = value
            else:
                identifiers[f"{ingredient}:{key}"] = value
    return identifiers


def _ambiguity_reason(candidates: list[NormalizationCandidate]) -> str | None:
    if len(candidates) <= 1:
        return None
    names = ", ".join(candidate.normalized_name for candidate in candidates)
    return f"Multiple medication identity candidates: {names}"
