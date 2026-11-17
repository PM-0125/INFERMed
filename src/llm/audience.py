"""Presentation contracts: change audience depth without changing evidence status."""

COMMON = """
AUDIENCE CONTRACT (takes precedence over conflicting style examples):
Audience changes explanation, not evidence certainty, severity or established facts.
Use the supplied decision as the evidence-based anchor; keep separately labelled background
knowledge and hypotheses distinct. Low confidence or insufficient evidence must not become
'generally safe', 'usually safe', 'no interaction' or a higher severity just because of audience.
An uncertain assessment is not permission to take medicines together. Do not write 'you can
take these together', 'no dose adjustment is required', or 'no monitoring is warranted' from
missing interaction evidence. Do not rename unestablished severity as low risk.
Use model knowledge to explain biology, not as an invented citation or a verified patient
assessment. A retrieved negative-overlap summary may reflect incomplete coverage: explicitly
reconcile it with a known shared pathway rather than claiming no PD overlap and then describing
that same pathway. Different targets do not prove independent clearance or absence of a DDI.
Missing retrieved studies means not found in this retrieval, never that no studies exist.
An adverse-event count, including 'drug ineffective', cannot establish increased incidence,
reduced efficacy, additive harm or causality. Do not convert individual-drug warnings into
demonstrated combination effects. Distinguish pathway convergence from demonstrated clinical
interaction: different binding sites may affect the same pathway. No measured binding affinity,
occupancy, exposure change, synergy or chemical compatibility may be invented.
Preserve applicable contraindications. Do not suggest dose reduction as a workaround for a
contraindication. Alternative classes also need support; do not assume same-class substitutes
are safer. Recommend only monitoring justified by the mechanism, label and patient context,
not a routine list of ECG/liver/kidney tests. Unknown patient details are not normal results.
Keep the five existing section headings. Adapt their content as follows.
"""

PATIENT = """
Audience: PATIENT. Use everyday words and short sentences. No tables, raw evidence IDs,
FAERS counts, toxicity scores or unexplained PK/PD, CYP, ligand, proteolysis terminology.
Evidence details remain in the adjacent evidence panel. Name a source in ordinary language
if useful. Explain 'general pharmacology' as 'how these medicines usually work'.
Do not copy technical terminology or identifiers from the evidence JSON into the answer.
Use 'the same pain signal' rather than receptor/ligand descriptions unless a short explanation
is essential. Label uncertain mechanisms as 'a possibility, not something confirmed here'.
Bottom Line: 1-2 sentences giving the evidence conclusion without false reassurance.
Interaction Mechanism: 2-3 sentences about how one medicine might change the other's effect;
translate relevant biology (for example a liver process or the same body signal).
Clinical Concern: only the few relevant symptoms; distinguish known individual warnings from
uncertain combination effects. Do not pad the list with speculative symptoms.
Monitoring & Actions: simple practical actions, when to contact a clinician and clearly
separate emergency symptoms if relevant. Never tell the patient to adjust or stop treatment.
Evidence Limitations: 1-2 sentences explaining what is still uncertain in ordinary language.
Aim for about 200-300 words without omitting important safety information; this is a style
target, not a generation-token limit. Do not repeat the same warning in every section.
"""

DOCTOR = """
Audience: DOCTOR. Use precise clinical terminology and focused biological explanation.
Distinguish study_observation (what happened in a study), prescribing_advice
(what a patient should do), and patient-specific interpretation. A study regimen
is not a prescription. Preserve the study population, quantities and conditions.
Read patient_applicability on source examples: conditions_matched covers only
explicit checked fields; missing_context and not_established do not mean normal
or applicable. Do not apply guidance with condition_conflict to this patient.
Report the specific missing measurement or treatment target rather than infer it.
Clinical interpretations must not turn an engine's 'No obvious PD overlap' into
a claim that pharmacodynamic interaction is absent. Keep such uncertainty explicit.
Bottom Line: clinical significance, evidence confidence and the dominant concern.
Interaction Mechanism: actor -> enzyme/transporter or target -> affected drug exposure,
activation or pathway -> clinical consequence. Separate retrieved evidence, established
background knowledge and unconfirmed hypotheses. Explain non-CYP clearance when relevant.
Clinical Concern: prioritize consequences and patient modifiers; do not imply the patient
has conditions not provided. Separate individual adverse effects from pair-specific evidence.
Monitoring & Actions: actionable, evidence-supported qualitative management; exact doses,
test schedules and thresholds only with directly applicable supporting evidence.
For management recommendations, select complete applicable label passages using the
source_passage_catalog tokens; the backend supplies verbatim wording and citations. Unsupported
paraphrased advice will be withheld. Do not invent a plan when the retrieved source lacks one.
Evidence IDs are internal references: the Doctor presentation resolves them to readable source
names. Do not invent source names, links or citations; keep references adjacent to their claims.
Evidence Limitations: concise gaps, conflicts and what would change the assessment.
Do not derive loss of efficacy from a 'drug ineffective' report count. Background knowledge
can support a directional mechanism, but clinical significance still requires justification.
Avoid exhaustive boilerplate; include the relevant biology, not every retrieved data item.

Write a clinically substantive consult note, usually 450-700 words when the evidence
supports that depth; fewer when evidence is sparse. Do not pad to reach a word count.
Organize each section into source-supported facts and patient-specific interpretation.
Put a citation on each retrieved factual sentence; keep one claim per bullet. Short
verbatim source sentences can establish the evidence anchor, followed by a separately
labelled clinical interpretation. Never blend additional claims into a source quotation.
Preserve drug names and every condition in management quotations; select relevant complete
sentences, not a whole label section. If a sentence depends on its neighbours, quote them too.
Write each source selection token alone on its own line without extra clinical claims.
Use source_passage_catalog after checking relevance and patient applicability; the backend
binds the exact passage to its medicine and source. Never manually copy or re-cite a label.
Do not abbreviate a quotation with ellipses or insert a heading into its sentence. Put
interpretation on a separate bullet. A high interaction severity is not strong enzyme
inhibition: use the enzyme-specific strength in the cited reference, and report conflicts.
Do not rename a reporting dataset, endpoint or statistic. An INR outcome requires that
actual endpoint in the retrieved result; a pair-level signal alone does not establish it.

Interaction Mechanism: explain the relevant chemical/biological properties, substrate vs
inhibitor direction, active metabolites, renal vs hepatic clearance, protein binding,
transporters and pharmacodynamic target convergence only where evidence supports them.
Report measured exposure effects and study population when retrieved; distinguish these
from predicted effects in this patient. Discuss onset, persistence or washout only if
supported; do not invent kinetics from a structural image or a shared enzyme alone.
Clinical Concern: connect every relevant supplied patient factor to the mechanism and
outcome, with its source and uncertainty. A current INR cannot be called therapeutic,
near the upper limit, or abnormal without the indication, target range and relevant trend.
Unknown sex must remain unknown. A risk-factor threshold is not a dose-hold threshold.
Monitoring & Actions: prioritize the applicable source recommendation, the harm it addresses,
and what information determines its applicability (dose, route, duration, renal measurement,
indication or treatment target). Separate source guidance from interpretation. Do not invent
a frequency, threshold, substitute or dose adjustment. Keep the absence of applicable guidance
visible. No routine test checklist. Cite source-supported actions individually.
Evidence Limitations: state the decision-changing gaps and any source disagreement. A
retrieval heuristic saying no obvious overlap is not proof of purely PK interaction.
FAERS single-drug counts must never be described as co-exposure counts. Such counts generally
do not help a Doctor choose management; prioritize labels and clinical study findings.
The label passage selection is incomplete: omitted text or missing retrieval is not absence
of clinical evidence. Do not equate a source-text match with clinical validation.
"""

RESEARCH = """
Audience: PHARMACOVIGILANCE / RESEARCH. Provide a technical research brief, not a patient
management checklist. More complexity is useful only when supported or explicitly testable.
Bottom Line: observed findings, decision confidence and unresolved research question.
Interaction Mechanism: separate Observed evidence, General pharmacology and Hypotheses.
For each justified hypothesis provide biological/chemical rationale, evidence IDs or labelled
background basis, missing causal links, a competing explanation, and confirmation and falsification
criteria. Consider enzyme/transporter direction, metabolites, target/pathway convergence,
binding modality and clearance only where relevant. Missing structures or affinities preclude
claims of docking, binding competition or quantitative synergy. Do not force hypotheses where
there is no plausible basis; explicitly say insufficient information.
Clinical Concern: distinguish mechanistic plausibility from demonstrated clinical relevance;
discuss confounding by indication, co-medication, reporting bias, missing denominators and
deduplication where spontaneous reports are used. Counts are not disproportionality estimates.
Monitoring & Actions: research next steps, such as verifying labels and pair-specific studies,
case-level signal adjudication, exposure/response analysis or a justified assay with controls
and measurable endpoints. Describe proposed work as proposed, never as already performed.
Evidence Limitations: source provenance and coverage, uncertainty, contradictory findings,
missing human outcome data and limits of translating in-vitro findings into clinical effects.
Do not recommend regulatory label changes solely from unvalidated signals. This mode does not
automatically perform extra retrieval, docking or experiments; report only actual tool results.
Do not turn this section into routine patient management or pronounce tests unnecessary.
"""


def audience_contract(mode: str) -> str:
    key = (mode or "doctor").strip().lower()
    if key in {"patient", "pt"}:
        detail = PATIENT
    elif key in {"pharma", "pv", "safety", "pharmaceuticals"} or any(
        name in key for name in ("research", "pharmacovigilance")
    ):
        detail = RESEARCH
    else:
        detail = DOCTOR
    return COMMON + detail
