"""Auditable text matches against source content, never keyword entailment."""

import re
import unicodedata

REFERENCE = re.compile(r"\bev_[A-Za-z0-9_]+\b")
CITATION = re.compile(r"[\[【][^\]】\n]*\bev_[^\]】\n]*[\]】]")
CONDITION = re.compile(
    r"\b(?:if|when|while|until|unless|only|except|however|provided|following|above|below|these|those|"
    r"such|this recommendation|this instruction|in (?:patients|subjects) with|"
    r"for (?:patients|subjects) with)\b",
    re.I,
)


def normalized(text):
    text = text.replace("‑", "-").replace("‐", "-")
    text = re.sub(r"\[see\s+[^\]]+\]", "", text, flags=re.I)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return (
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))
        .strip()
        .lower()
        .rstrip(".")
    )


def plain(text):
    text = CITATION.sub("", text).replace("**", "").replace("__", "")
    text = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"\s*Evidence:\s*[.,;\s]*$", "", text, flags=re.I)
    text = re.sub(r"^Source guidance \([\w ,+/-]+\):\s*", "", text, flags=re.I)
    text = text.strip()
    wrapper = re.fullmatch(
        r'Source statement:\s*["“]([^"“”]+)["”][.]?', text, re.S | re.I
    )
    if wrapper:
        text = wrapper[1]
    if re.match(r'^["“].*["”][.]?$', text, re.S):
        text = re.sub(r'["”][.]?$', "", text[1:])
    return text


def units(line):
    """Keep citations with the preceding sentence; never split decimal numbers."""
    return [
        s.strip()
        for s in re.split(
            r'(?<=[.!?])\s+(?!\[ev_|Evidence:\s*\[ev_)(?=[A-Z*“"])|(?<=[.!?][)”"])\s+(?=[A-Z])|(?<=\])\s+(?=[A-Z])',
            line.strip(),
        )
        if s.strip()
    ]


def guidance_units(line):
    """Separate a named source quotation from surrounding model commentary."""
    pattern = re.compile(
        r'Source guidance \([\w ,+/-]+\):\s*[“"]([^”"]+)[”"]\s*(?:[\[【][^\]】\n]*ev_[^\]】\n]*[\]】]\s*)+',
        re.I,
    )
    parts, end = [], 0
    for match in pattern.finditer(line):
        prefix = line[end : match.start()].strip(" \t–—-")
        if prefix:
            # Formatting alone never makes a qualifier disposable. Only these
            # known section labels can be removed from an inline quotation.
            if plain(prefix).lower() not in {
                "inr monitoring",
                "bleeding risk",
                "dose adjustment",
                "source guidance",
                "label guidance",
                "monitoring",
            }:
                return [line]
        parts.append(match[0].strip())
        end = match.end()
    if not parts:
        return [line]
    tail = line[end:].strip(" \t–—-")
    if CONDITION.match(plain(tail)) or re.match(
        r"(?:this|the (?:above|preceding|quoted) (?:advice|guidance|recommendation))\b",
        plain(tail),
        re.I,
    ):
        return [line]
    if tail:
        parts.append(tail)
    return parts


def partial_source_quotes(text, cards, scope=()):
    """Expose exact quoted fragments; never certify surrounding interpretation."""
    matches = []
    for quote in re.findall(r'[“"]([^”"]+)[”"]', text):
        pieces = [
            normalized(p).strip() for p in re.split(r"…|\.{3}", quote) if p.strip()
        ]
        if not pieces or sum(len(p) for p in pieces) < 24:
            continue
        for card in cards:
            for passage in source_passages(card):
                if not passage_covers_scope(card, passage, scope):
                    continue
                haystack = normalized(passage["text"])
                # Ordered literal fragments only; an ellipsis remains partial.
                cursor = 0
                for piece in pieces:
                    pos = haystack.find(piece, cursor)
                    if pos < 0:
                        break
                    cursor = pos + len(piece)
                else:
                    matches.append(
                        {
                            "evidence_id": card.evidence_id,
                            "source_name": card.source_name,
                            "section": passage["section"],
                            "text": passage["text"],
                            "context": passage["context"],
                            "source_url": (
                                card.payload.get("provenance") or card.provenance
                            ).get("source_url"),
                        }
                    )
                    break
    return matches


def fda_strength_conflicts(text, cards):
    reasons = []
    for card in cards:
        if card.claim_type != "mechanism_reference":
            continue
        for entry in card.payload.get("matches", []):
            row = entry.get("row") or {}
            enzyme = str(row.get("Enzyme") or "")
            if not enzyme:
                continue
            for drug in card.drug_scope:
                claim = re.search(
                    r"\b"
                    + re.escape(drug)
                    + r"\s+(strongly|moderately|weakly)\s+inhibits\s+"
                    + re.escape(enzyme)
                    + r"\b",
                    text,
                    re.I,
                )
                if not claim:
                    continue
                prefix = re.split(r"[.!?]", text[: claim.start()])[-1]
                if re.search(
                    r"\b(?:not|no|incorrect|false|wrong|untrue|unsupported|uncertain|whether)\b",
                    prefix,
                    re.I,
                ):
                    continue
                asserted = {
                    "strongly": "strong",
                    "moderately": "moderate",
                    "weakly": "weak",
                }[claim[1].lower()]
                for heading, value in row.items():
                    strength = next(
                        (
                            s
                            for s in ("strong", "moderate", "weak")
                            if s in heading.lower()
                        ),
                        None,
                    )
                    if (
                        strength
                        and "inhibitor" in heading.lower()
                        and re.search(
                            r"(?<!\w)" + re.escape(drug) + r"(?!\w)", str(value), re.I
                        )
                        and asserted != strength
                    ):
                        reasons.append(
                            f"Cited FDA row lists {drug} as a {strength} inhibitor of {enzyme}; the claim says {asserted}."
                        )
    return reasons


def source_passages(card, *, actions=False):
    if actions and (
        card.payload.get("action_support_eligible") is False
        or card.evidence_grade not in {"label", "guideline"}
        or card.source_type
        in {"reasoning_engine", "mechanism_engine", "normalized_context"}
    ):
        return []
    rows = []
    if card.claim_type != "label_context":
        rows.append(("claim", card.claim_text))
    # Selected passages retain their boundaries and adjacent conditions. Never
    # parse stitched sections across omitted-content markers as continuous text.
    selected = card.payload.get("passages")
    if isinstance(selected, list):
        rows.extend(
            (p.get("section", "label"), p.get("text", ""))
            for p in selected
            if p.get("content_kind") != "structured_table_section"
        )
    else:
        sections = card.payload.get("sections") or {}
        if isinstance(sections, dict):
            rows.extend((k, v) for k, v in sections.items() if isinstance(v, str))
    result = []
    for section, text in rows:
        if not text.strip() or text.rstrip().endswith(("...", "…")):
            continue
        result.append({"section": section, "text": text, "context": text})
        sentences = units(text)
        for i, sentence in enumerate(sentences):
            neighbours = sentences[max(0, i - 1) : i] + sentences[i + 1 : i + 2]
            # Context-dependent sentence extraction is intentionally withheld.
            # The full passage may still match with its qualifications intact.
            if any(CONDITION.search(s) for s in neighbours):
                continue
            if len(sentence.split()) >= 4:
                result.append({"section": section, "text": sentence, "context": text})
    return result


def passage_covers_scope(card, passage, scope):
    missing = set(scope) - set(card.drug_scope)
    return not missing or (
        card.claim_type == "label_context"
        and all(
            re.search(r"(?<!\w)" + re.escape(d) + r"(?!\w)", passage["text"], re.I)
            for d in missing
        )
    )


def match_sources(text, cards, scope, *, actions=False):
    matches = []
    target = normalized(plain(text))
    for card in cards:
        for passage in source_passages(card, actions=actions):
            # Product labels can support an exact passage about a named partner.
            if not passage_covers_scope(card, passage, scope):
                continue
            if target == normalized(passage["text"]):
                matches.append(
                    {
                        "evidence_id": card.evidence_id,
                        "source_name": card.source_name,
                        "section": passage["section"],
                        "text": passage["text"],
                        "context": passage["context"],
                        "source_url": (
                            card.payload.get("provenance") or card.provenance
                        ).get("source_url"),
                    }
                )
                break
    return matches
