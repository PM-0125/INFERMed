from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase
from src.domain.medication.services import normalize_medication
from src.infrastructure.persistence.event_store import SQLiteEventStore


def test_medication_concept_is_stable_for_normalized_text():
    a = normalize_medication(" Warfarin ")
    b = normalize_medication("warfarin")

    assert a.normalized_name == "warfarin"
    assert a.concept_id == b.concept_id
    assert a.ingredients == ["warfarin"]


def test_event_store_persists_analysis_events_and_evidence(tmp_path):
    store = SQLiteEventStore(tmp_path / "audit.sqlite")

    def fake_rag_runner(*args, **kwargs):
        return {
            "context": {
                "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
                "signals": {
                    "tabular": {"prr": 3.2},
                    "faers": {"combo_reactions": [["bleeding", 3]]},
                    "mechanistic": {},
                },
                "pkpd": {
                    "pk_summary": "PK overlap present",
                    "pd_summary": "No PD overlap",
                    "pk_detail": {"overlaps": {"inhibition": ["cyp2c9"]}},
                    "pd_detail": {},
                },
            },
            "answer": {"text": "## Bottom Line\nMonitor closely."},
        }

    use_case = AnalyzeMedicationSetUseCase(rag_runner=fake_rag_runner, event_store=store)
    result = use_case.execute(
        AnalyzeMedicationSetCommand(
            medications=["warfarin", "fluconazole"],
            audience="doctor",
        )
    )

    events = store.list_events(result.analysis_id)
    event_types = [event["event_type"] for event in events]
    assert "MedicationSetAnalysisRequested" in event_types
    assert "EvidencePlanCreated" in event_types
    assert "ClinicalDecisionCreated" in event_types
    assert result.decision.risk_level == "major"
    assert result.evidence_cards
    assert result.mechanism_graph.nodes


def test_use_case_keeps_current_pair_runner_contract():
    calls = {}

    def fake_rag_runner(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {
            "context": {
                "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
                "signals": {"tabular": {}, "faers": {}, "mechanistic": {}},
                "pkpd": {"pk_detail": {}, "pd_detail": {}},
            },
            "answer": {"text": "Plain answer"},
        }

    result = AnalyzeMedicationSetUseCase(rag_runner=fake_rag_runner).execute(
        AnalyzeMedicationSetCommand(
            medications=[" Warfarin ", "Fluconazole", "Ibuprofen"],
            audience="pv_research",
            refresh_evidence=True,
        )
    )

    assert calls["args"] == ("warfarin", "fluconazole")
    assert calls["kwargs"]["mode"] == "pv_research"
    assert calls["kwargs"]["use_cache_context"] is False
    assert calls["kwargs"]["use_cache_response"] is False
    assert calls["kwargs"]["model_name"] is None
    assert result.evidence_plan.pair_count == 3
    assert result.executed_pair == ("warfarin", "fluconazole")

