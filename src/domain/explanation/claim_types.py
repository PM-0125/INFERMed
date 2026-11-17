"""Conservative speech-act classification, separate from evidence verification.

Recognizing a reported study does not establish that the report is true or that
its regimen applies to a patient. Ambiguous and mixed instructions stay gated.
"""

import re
from src.domain.explanation.passages import plain, units

DIRECTIVE_PATTERN = (
    r"\b(?:monitor(?:ing)?|checks?|recheck|repeat|measure|test|screen|"
    r"prescrib\w*|administer(?!ed\b)\w*|titrate|taper|discontinue|withhold|avoid|"
    r"switch\w*|substitut\w*|counsel\w*|educat\w*|instruct|seek|contact|"
    r"consult|refer|recommend\w*|advise\w*|consider|obtain|order|"
    r"should|must|shall|warrant\w*|required|necessary|stop|start|restart|resume|take|hold|"
    r"double|halve|continue|maintain|unchanged|initiate|omit|suspend|return|"
    r"use|give|therefore|thus|is to be|are to be|"
    r"need(?:s)? to|can be used|may be used|dose(?!\s+of\b)|dosage|dosing)\b"
)
_INSTRUCTION = re.compile(DIRECTIVE_PATTERN, re.I)


def is_study_observation(text):
    text = plain(text)
    sentences = units(text)
    if len(sentences) > 1:
        # Positive grammar for a bounded statistical continuation. A cohort in
        # sentence one never exempts arbitrary later sentences from action checks.
        vocabulary = set(
            "there was were a an the mean sd increase increased decrease decreased in of prothrombin time response area under curve range see precautions is based on data from subjects as one two experienced fold his her exposure by had to".split()
        )
        return is_study_observation(sentences[0]) and all(
            re.match(
                r"^(?:There (?:was|were)|Mean is based|Exposure (?:increased|decreased)|One of)\b",
                sentence,
            )
            and set(re.findall(r"[a-z]+", sentence.lower())).issubset(vocabulary)
            for sentence in sentences[1:]
        )
    if _INSTRUCTION.search(text) or re.search(
        r"\b(?:reduce|lower|raise)\b|\b(?:increase|decrease)\s+(?!in\b|of\b)",
        text,
        re.I,
    ):
        return False
    # Mixed semicolon clauses require a recognized reported measurement, not an
    # arbitrary suffix that might contain a new instruction verb.
    for clause in text.split(";")[1:]:
        if not re.fullmatch(
            r"\s*(?:exposure|auc|cmax|concentration|clearance|half-life)\s+(?:increased|decreased|changed|was|were)\b[^;]+",
            clause,
            re.I,
        ):
            return False
    population = re.search(
        r"\b\d+\s+(?:(?:healthy|normal|adult|male|female|young|elderly)\s+)*(?:volunteers|subjects|participants|patients)\b",
        text,
        re.I,
    )
    observed = re.search(
        r"\b(?:was|were)\s+(?:administered|given|observed|measured|reported)|\b(?:administered to|received|increased by|decreased by)\b",
        text,
        re.I,
    )
    return bool(population and observed)


def claim_kind(text, action_candidate=False):
    if is_study_observation(text):
        return "study_observation"
    return "prescribing_advice" if action_candidate else "descriptive_claim"
