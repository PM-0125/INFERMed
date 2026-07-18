from src.application.use_cases.answer_followup import AnswerFollowUpCommand, AnswerFollowUpUseCase
from src.application.source_tools import SourceToolInput, SourceToolResult
from src.domain.decision.rules import missing_patient_factors_for_decision, patient_amplifiers
from src.domain.decision.scoring import score_pair_context
from src.domain.evidence.entities import EvidenceCard
from src.domain.evidence.hierarchy import rank_evidence_cards, strongest_evidence_grade
from src.domain.explanation.verifier import ExplanationVerifier
from src.domain.mechanism.entities import MechanismCluster, MechanismGraph
from src.domain.mechanism.engines import detect_pd_mechanisms, detect_pk_mechanisms
from src.domain.medication.services import normalize_medication
from src.domain.patient.services import normalize_patient_context
from src.domain.profile.entities import DrugProfileGraph
from src.domain.reasoning.entities import InteractionReasoningRecord
from src.domain.reasoning.services import MedicationSetReasoner
from src.domain.research.services import ResearchHypothesisBuilder
from src.domain.safety.entities import SafetyReport
from src.infrastructure.persistence.cache_store import SQLiteToolCacheStore
from src.infrastructure.tools.executor import CachedToolExecutor
from src.domain.decision.entities import InteractionDecision


def test_patient_context_normalization_preserves_nested_objects():
    context = normalize_patient_context(
        {
            "age": 78,
            "renal_function": "severe_impairment",
            "qt_risk": "known_long_qt",
            "labs": [{"name": "creatinine", "value": 2.1, "unit": "mg/dL"}],
            "pgx": [{"gene": "CYP2C9", "variant": "*2"}],
        }
    )

    assert context.age_years == 78
    assert context.renal_function == "severe_impairment"
    assert context.labs[0].name == "creatinine"
    assert context.genotypes[0].gene == "CYP2C9"
    assert "advanced age" in patient_amplifiers(context)
    assert "renal impairment" in patient_amplifiers(context)
    assert "hepatic function" in missing_patient_factors_for_decision(context)


def test_evidence_hierarchy_ranks_label_before_signal():
    signal = _card("1", "OpenFDA FAERS", "pharmacovigilance_signal")
    label = _card("2", "DailyMed SPL", "label")

    ranked = rank_evidence_cards([signal, label])

    assert ranked[0].evidence_grade == "label"
    assert strongest_evidence_grade([signal, label]) == "label"


def test_decision_scoring_and_mechanism_engines_are_domain_owned():
    context = {
        "signals": {
            "tabular": {"prr": 3.0},
            "faers": {"combo_reactions": [["bleeding", 4]]},
        },
        "pkpd": {
            "pk_summary": "inhibits CYP2C9",
            "pk_detail": {"canonical_interaction": {"rule": "x"}, "overlaps": {"inhibition": ["CYP2C9"]}},
            "pd_detail": {"overlap_targets": ["PTGS1"]},
        },
    }
    cards = [_card("pk", "PK/PD", "mechanistic", claim_type="pk_mechanism")]

    risk, confidence, score = score_pair_context(context)
    pk_graph = detect_pk_mechanisms(context, cards, ["warfarin", "fluconazole"])
    pd_graph = detect_pd_mechanisms(context, cards, ["warfarin", "fluconazole"])

    assert risk == "major"
    assert confidence == "high"
    assert score >= 4
    assert pk_graph.clusters[0].risk_type == "PK"
    assert pd_graph.clusters[0].risk_type == "PD"


def test_explanation_verifier_returns_structured_grounding_report():
    decision = InteractionDecision(
        analysis_id="ana_1",
        interaction_scope="pair",
        drugs=["a", "b"],
        risk_level="major",
        confidence="high",
        evidence_grade="mechanistic",
    )
    report = SafetyReport(analysis_id="ana_1", allow_generation=True, requires_review=False)

    verification = ExplanationVerifier().verify(
        answer_text="Monitor closely.",
        decision=decision,
        evidence_cards=[_card("1", "PK/PD", "mechanistic")],
        safety_report=report,
    )

    assert verification.checked is True
    assert verification.risk_level == "major"
    assert verification.evidence_card_count == 1


def test_sqlite_tool_cache_uses_semantic_stable_keys(tmp_path):
    store = SQLiteToolCacheStore(tmp_path / "cache.sqlite")
    key_a = store.key_for(tool_name="fetch_pubchem_compound", tool_version="1", input_payload={"drug": "warfarin"})
    key_b = store.key_for(tool_name="fetch_pubchem_compound", tool_version="1", input_payload={"drug": "warfarin"})

    store.put(
        cache_key=key_a,
        tool_name="fetch_pubchem_compound",
        source_name="PubChem",
        input_payload={"drug": "warfarin"},
        payload={"cid": 54678486},
    )

    assert key_a == key_b
    assert store.get(key_b) == {"cid": 54678486}


def test_medication_identity_handles_alias_combination_and_ambiguity():
    brand = normalize_medication("Coumadin 5 mg tablet")
    combo = normalize_medication("warfarin + aspirin")
    ambiguous = normalize_medication("ASA")

    assert brand.normalized_name == "warfarin"
    assert brand.confidence == "alias_match"
    assert brand.dose_route_form.dose == "5 mg"
    assert combo.confidence == "combination_product"
    assert combo.ingredients == ["warfarin", "aspirin"]
    assert ambiguous.confidence == "ambiguous"
    assert len(ambiguous.candidates) >= 2


def test_cached_tool_executor_reuses_source_result_by_normalized_input(tmp_path):
    class DummyTool:
        name = "fetch_dummy"
        source_name = "DummySource"
        version = "2026-01"

        def __init__(self):
            self.calls = 0

        def execute(self, tool_input):
            self.calls += 1
            return SourceToolResult(
                tool_name=tool_input.tool_name,
                source_name=tool_input.source_name,
                ok=True,
                normalized_payload={"drug": "warfarin", "call": self.calls},
            )

    tool = DummyTool()
    executor = CachedToolExecutor(SQLiteToolCacheStore(tmp_path / "cache.sqlite"))
    source_input = SourceToolInput(
        tool_name=tool.name,
        source_name=tool.source_name,
        query_type="drug_profile",
        normalized_concept_ids=["med_b", "med_a"],
        parameters={"drug": "warfarin"},
        source_version=tool.version,
    )

    first = executor.execute(tool, source_input)
    second = executor.execute(tool, source_input)

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.normalized_payload["drug"] == "warfarin"
    assert tool.calls == 1


def test_reasoning_and_research_summaries_expose_clusters_gaps_and_research_signals():
    graph = MechanismGraph(
        clusters=[
            MechanismCluster(
                cluster_id="cl_1",
                label="Shared QT and adverse-event convergence",
                risk_type="toxicity",
                drivers=["qt_prolongation"],
                affected_drugs=["a", "b", "c"],
                confidence="medium",
                evidence_ids=["ev_1"],
            )
        ]
    )
    profile = DrugProfileGraph(
        analysis_id="ana_1",
        drugs=["a", "b", "c"],
        missing_elements={"a": ["label_section"], "b": ["target"], "c": []},
    )
    record = InteractionReasoningRecord(
        analysis_id="ana_1",
        drugs=["a", "b", "c"],
        known_status="unknown_mechanistically_plausible",
    )
    context = {
        "signals": {
            "research_enrichment": {
                "europe_pmc": {"hit_count": 2},
                "string": {"network": "available"},
                "nci_almanac": {"combination_rows": 1},
            }
        }
    }

    summary = MedicationSetReasoner().summarize(
        drugs=["a", "b", "c"],
        pair_summaries=[
            {"pair": ["a", "b"], "risk_level": "moderate"},
            {"pair": ["a", "c"], "risk_level": "major"},
        ],
        mechanism_graph=graph,
        profile_graph=profile,
        reasoning_record=record,
    ).to_dict()
    signals = ResearchHypothesisBuilder().build(context=context, profile_graph=profile)

    assert summary["pair_count"] == 2
    assert summary["top_pairs"][0]["pair"] == ["a", "c"]
    assert summary["clusters"][0]["risk_type"] == "toxicity"
    assert summary["hypotheses"][0]["support_level"] == "insufficient"
    assert {signal.signal_type for signal in signals} >= {"literature", "protein_network", "experimental_combo", "evidence_gap"}


def test_followup_use_case_prefers_analysis_snapshot():
    class SnapshotStore:
        def get_analysis_snapshot(self, analysis_id):
            return {"context": {"meta": {"analysis_id": analysis_id}, "drugs": {"a": {"name": "a"}, "b": {"name": "b"}}}}

    calls = {"context_runner": 0}

    def context_runner(*args, **kwargs):
        calls["context_runner"] += 1
        return {}

    def generator(context, mode, **kwargs):
        return {"text": f"Scoped to {context['meta']['analysis_id']}"}

    use_case = AnswerFollowUpUseCase(
        followup_generator=generator,
        context_runner=context_runner,
        cited_cards_from_context=lambda context: ["card-1"],
        snapshot_store=SnapshotStore(),
    )
    answer = use_case.execute(
        AnswerFollowUpCommand(question="What changes?", drugs=["a", "b"], context_id="ana_1")
    )

    assert answer.answer == "Scoped to ana_1"
    assert answer.context_source == "analysis_snapshot"
    assert calls["context_runner"] == 0


def test_followup_use_case_compacts_repeated_answer_and_adds_patient_context():
    class SnapshotStore:
        def get_analysis_snapshot(self, analysis_id):
            return {"context": {"meta": {"analysis_id": analysis_id}, "medication_set": {}}}

    seen_context = {}

    def generator(context, mode, **kwargs):
        seen_context.update(context)
        return {
            "text": (
                "## Bottom Line\nRepeated initial answer.\n\n"
                "## Direct Answer\nMonitor renal function and bleeding symptoms for this patient.\n\n"
                "## Monitoring & Actions\nRepeated monitoring block."
            )
        }

    use_case = AnswerFollowUpUseCase(
        followup_generator=generator,
        context_runner=lambda *args, **kwargs: {},
        cited_cards_from_context=lambda context: ["patient-context"],
        snapshot_store=SnapshotStore(),
    )
    answer = use_case.execute(
        AnswerFollowUpCommand(
            question="What changes with renal impairment?",
            drugs=["warfarin", "fluconazole"],
            context_id="ana_1",
            patient_context={"renal_function": "severe_impairment"},
        )
    )

    assert "Bottom Line" not in answer.answer
    assert "Direct Answer" in answer.answer
    assert seen_context["medication_set"]["patient_context"]["renal_function"] == "severe_impairment"


def _card(evidence_id: str, source: str, grade: str, *, claim_type: str = "claim") -> EvidenceCard:
    return EvidenceCard(
        evidence_id=f"ev_{evidence_id}",
        analysis_id="ana_1",
        source_name=source,
        source_type="test",
        claim_type=claim_type,
        claim_text=f"{source} claim",
        evidence_grade=grade,
        drug_scope=["a", "b"],
    )
