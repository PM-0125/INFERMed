"""Bound label context while preserving auditable, contiguous source passages.

Selection is a retrieval heuristic, not a finding of clinical applicability. Offsets
always refer to the complete normalized section retained by the label client.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

MAX_SELECTED_CHARS = 12000
OMISSION_MARKER = "\n\n[... omitted passages ...]\n\n"
_MECHANISM = re.compile(
    r"\b(?:CYP\w*|inhibit\w*|induc\w*|substrate\w*|metaboli\w*|transporter\w*)\b", re.I
)
_PATIENT_TOPICS = {
    "renal": r"\b(?:renal|kidney\w*|egfr|creatinine|dialysis)\b",
    "hepatic": r"\b(?:hepatic|liver|cirrhosis)\b",
    "pregnancy": r"\b(?:pregnan\w*|lactat\w*|breast\w*|nursing)\b",
    "age": r"\b(?:age|aged|older|elderly|geriatric\w*|pediatric\w*)\b",
    "qt": r"\b(?:qt\w*|arrhythm\w*|torsade\w*)\b",
}
_CONDITION = re.compile(
    r"\b(?:only|unless|except|if|when|provided|under these conditions)\b", re.I
)


def _patient_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(key).replace("_", " ") + " " + _patient_text(item)
            for key, item in value.items()
            if _patient_text(item)
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_patient_text(item) for item in value)
    text = str(value or "").strip().casefold()
    return (
        ""
        if text in {"", "unknown", "none", "not known", "not provided", "unspecified"}
        else text
    )


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    cursor = 0
    for end, next_start in [
        (m.start(), m.end()) for m in re.finditer(r"\n[ \t]*\n", text)
    ] + [(len(text), len(text))]:
        start = cursor
        cursor = next_start
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
    return spans


def _context_spans(text: str) -> list[tuple[int, int]]:
    """Keep small paragraphs whole; long paragraphs use three-sentence windows."""
    spans = []
    paragraphs = _paragraph_spans(text)
    for paragraph_index, (start, end) in enumerate(paragraphs):
        context_start = start
        context_end = end
        if paragraph_index + 1 < len(paragraphs):
            following_start, following_end = paragraphs[paragraph_index + 1]
            if _CONDITION.search(text[following_start:following_end]):
                context_end = following_end
        if paragraph_index:
            previous_start, previous_end = paragraphs[paragraph_index - 1]
            previous = text[previous_start:previous_end]
            if _CONDITION.search(previous) or previous.endswith(":"):
                context_start = previous_start
        if end - start <= 1600:
            spans.append((context_start, context_end))
            continue
        paragraph = text[start:end]
        boundaries = (
            [0]
            + [
                match.end()
                for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9])", paragraph)
            ]
            + [len(paragraph)]
        )
        sentences = [
            (start + a, start + b) for a, b in zip(boundaries, boundaries[1:]) if a < b
        ]
        for index in range(len(sentences)):
            left = sentences[max(0, index - 1)][0]
            if left == start:
                left = context_start
            right = sentences[min(len(sentences) - 1, index + 1)][1]
            if right == end:
                right = context_end
            while right > left and text[right - 1].isspace():
                right -= 1
            spans.append((left, right))
    return list(dict.fromkeys(spans))


def _merge(candidates: list[dict], sections: dict[str, str]) -> list[dict]:
    merged: list[dict] = []
    order = {section: index for index, section in enumerate(sections)}
    for candidate in sorted(
        candidates, key=lambda p: (order[p["section"]], p["start"])
    ):
        if merged and merged[-1]["section"] == candidate["section"]:
            previous = merged[-1]
            gap = sections[candidate["section"]][previous["end"] : candidate["start"]]
            if candidate["start"] <= previous["end"] or not gap.strip():
                previous["end"] = max(previous["end"], candidate["end"])
                previous["reasons"].update(candidate["reasons"])
                continue
        merged.append(
            {
                "section": candidate["section"],
                "start": candidate["start"],
                "end": candidate["end"],
                "reasons": set(candidate["reasons"]),
            }
        )
    return merged


def _render(passages: list[dict], sections: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for passage in passages:
        section = passage["section"]
        text = sections[section][passage["start"] : passage["end"]]
        result[section] = (
            result[section] + OMISSION_MARKER + text if section in result else text
        )
    return result


def select_label_passages(
    label: dict, drugs: list[str], patient_context: dict | None = None
) -> dict[str, Any]:
    """Select complete passages, with neighbors, under a 12,000-character budget.

    Original metadata is retained, but raw response snapshots stay in the cache.
    Disconnected ranges are explicitly marked in ``sections``. ``passages`` are
    authoritative for citation offsets; ``selection`` describes omissions.
    """
    # Callers may reuse already-selected evidence. Selecting those strings again
    # would erase omissions and falsely claim their offsets belong to the source.
    if label.get("selection") and "passages" in label:
        return deepcopy(label)
    sections = {
        str(k): v
        for k, v in (label.get("sections") or {}).items()
        if isinstance(v, str) and v.strip()
    }
    structural = {}
    if isinstance(label.get("source_units"), list):
        # These units are produced by the SPL adapter before flattening. Never
        # reconstruct structure from selected text or certify sentence windows.
        sections = {}
        for index, unit in enumerate(label["source_units"]):
            if unit.get("content_kind") == "structured_table_section":
                continue  # Retained in source_units/tables, not prose or its budget.
            key = unit["section"]
            if key in sections:
                key += "::" + str(index)
            sections[key] = unit["text"]
            structural[key] = unit
    own_names = {str(label.get("query") or "").strip().casefold()}
    for field in ("generic_names", "brand_names", "substance_names"):
        own_names.update(
            str(name).strip().casefold() for name in label.get(field, []) or []
        )
    partners = sorted(
        {str(drug).strip().casefold() for drug in drugs if str(drug).strip()}
        - own_names
    )
    patterns = {
        partner: re.compile(r"(?<!\w)" + re.escape(partner) + r"(?!\w)", re.I)
        for partner in partners
    }
    patient_text = _patient_text(patient_context or {})
    topics = {
        topic: pattern
        for topic, pattern in _PATIENT_TOPICS.items()
        if re.search(pattern, patient_text, re.I)
    }
    candidates = []
    for section, text in sections.items():
        for start, end in (
            [(0, len(text))] if section in structural else _context_spans(text)
        ):
            passage = text[start:end]
            matched = [
                partner
                for partner, pattern in patterns.items()
                if pattern.search(passage)
            ]
            reasons = {"partner:" + partner for partner in matched}
            score = 100 if matched else 0
            if section == "boxed_warning":
                reasons.add("boxed_warning")
                score += 90
            for topic, pattern in topics.items():
                if re.search(pattern, passage, re.I):
                    reasons.add("patient_context:" + topic)
                    score += 35
            if _MECHANISM.search(passage):
                reasons.add("mechanism")
                score += 20
            if section in {
                "contraindications",
                "drug_interactions",
                "warnings",
                "warnings_and_cautions",
                "dosage_and_administration",
                "precautions",
            }:
                reasons.add("safety_section")
                score += 10
            candidates.append(
                {
                    "section": section,
                    "start": start,
                    "end": end,
                    "reasons": reasons or {"section_context"},
                    "score": score,
                }
            )
    ranked = sorted(candidates, key=lambda p: -p["score"])
    # Reserve one passage for each partner and each important evidence category.
    # A frequent early partner cannot consume the budget before a later one.
    categories = (
        ["boxed_warning"]
        + ["partner:" + partner for partner in partners]
        + ["patient_context:" + topic for topic in topics]
        + ["mechanism"]
    )
    reserved = []
    for category in categories:
        match = next(
            (
                item
                for item in ranked
                if category in item["reasons"]
                and item["end"] - item["start"] <= MAX_SELECTED_CHARS
            ),
            None,
        )
        if match is not None and match not in reserved:
            reserved.append(match)
    selected: list[dict] = []
    for candidate in reserved + [item for item in ranked if item not in reserved]:
        trial = _merge(selected + [candidate], sections)
        rendered = _render(trial, sections)
        if len("\n".join(rendered.values())) <= MAX_SELECTED_CHARS:
            selected = trial
    rendered = _render(selected, sections)
    passages = []
    omissions = []
    for section, text in sections.items():
        cursor = 0
        for item in (p for p in selected if p["section"] == section):
            if text[cursor : item["start"]].strip():
                omissions.append(
                    {
                        "section": section,
                        "start": cursor,
                        "end": item["start"],
                        "chars": item["start"] - cursor,
                    }
                )
            passages.append(
                {
                    "section": section,
                    "text": text[item["start"] : item["end"]],
                    "start": item["start"],
                    "end": item["end"],
                    "selection_reason": sorted(item["reasons"]),
                    "section_complete": item["start"] == 0 and item["end"] == len(text),
                    "context_policy": "whole_paragraph_or_adjacent_sentences",
                    **(
                        {
                            "section_path": structural[section]["section_path"],
                            "parent_context": structural[section]["parent_context"],
                            "content_kind": structural[section].get(
                                "content_kind", "prose"
                            ),
                            "context_policy": structural[section]["boundary_policy"],
                        }
                        if section in structural
                        else {}
                    ),
                }
            )
            cursor = item["end"]
        if text[cursor:].strip():
            omissions.append(
                {
                    "section": section,
                    "start": cursor,
                    "end": len(text),
                    "chars": len(text) - cursor,
                }
            )
    result = {
        key: value
        for key, value in label.items()
        if key not in {"sections", "raw_response", "passages", "selection"}
    }
    result.update(
        {
            "sections": rendered,
            "passages": passages,
            "selection": {
                "schema_version": 1,
                "structured_table_units_retained": sum(
                    u.get("content_kind") == "structured_table_section"
                    for u in label.get("source_units", [])
                ),
                "max_chars": MAX_SELECTED_CHARS,
                "selected_chars": sum(p["end"] - p["start"] for p in passages),
                "rendered_chars": len("\n".join(rendered.values())),
                "full_chars": sum(len(text) for text in sections.values()),
                "selected_passages": len(passages),
                "omitted_passages": len(omissions),
                "omitted_chars": sum(item["chars"] for item in omissions),
                "omitted_ranges": omissions,
                "full_sections": len(sections),
                "selected_sections": len(rendered),
                "requested_partners": partners,
                "unmatched_partners": [
                    p
                    for p in partners
                    if not any(patterns[p].search(text) for text in rendered.values())
                ],
                "limitation": "Selection does not establish clinical applicability or absence of interactions; complete source remains in the label cache.",
            },
        }
    )
    return result
