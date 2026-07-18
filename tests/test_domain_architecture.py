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
    assert result.profile_graph.nodes
    assert result.reasoning_record.known_status in {
        "known_signal_supported",
        "unknown_mechanistically_plausible",
    }
    assert "DrugProfileGraphBuilt" in event_types
    assert "InteractionReasoningRecordBuilt" in event_types
    assert "SafetyReportCreated" in event_types


def test_use_case_keeps_current_pair_runner_contract():
    calls = []

    def fake_rag_runner(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
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

    assert [call["args"] for call in calls] == [
        ("warfarin", "fluconazole"),
        ("warfarin", "ibuprofen"),
        ("fluconazole", "ibuprofen"),
    ]
    assert all(call["kwargs"]["mode"] == "pv_research" for call in calls)
    assert all(call["kwargs"]["use_cache_context"] is False for call in calls)
    assert all(call["kwargs"]["use_cache_response"] is False for call in calls)
    assert all(call["kwargs"]["model_name"] is None for call in calls)
    assert result.evidence_plan.pair_count == 3
    assert result.executed_pair == ("warfarin", "fluconazole")
    assert result.executed_pairs == [
        ("warfarin", "fluconazole"),
        ("warfarin", "ibuprofen"),
        ("fluconazole", "ibuprofen"),
    ]
    assert result.profile_graph.drugs == ["warfarin", "fluconazole", "ibuprofen"]


def test_profile_graph_and_reasoning_model_unknown_mechanistic_case():
    def fake_rag_runner(*args, **kwargs):
        return {
            "context": {
                "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
                "signals": {
                    "tabular": {},
                    "faers": {
                        "top_reactions_a": [("nausea", 4)],
                        "top_reactions_b": [("nausea", 3)],
                    },
                    "mechanistic": {
                        "enzymes_a": ["CYP3A4"],
                        "enzymes_b": ["CYP3A4"],
                        "targets_a": ["PTGS1"],
                        "targets_b": ["PTGS1"],
                    },
                },
                "pkpd": {
                    "pk_detail": {"overlaps": {"shared_enzymes": ["CYP3A4"]}},
                    "pd_detail": {"overlap_targets": ["PTGS1"]},
                },
            },
            "answer": {"text": "This is a plausible hypothesis because evidence is limited."},
        }

    result = AnalyzeMedicationSetUseCase(rag_runner=fake_rag_runner).execute(
        AnalyzeMedicationSetCommand(medications=["drug-a", "drug-b"])
    )

    assert result.reasoning_record.known_status == "unknown_mechanistically_plausible"
    assert any(signal.category == "pk_overlap" for signal in result.reasoning_record.signals)
    assert any(signal.category == "pd_overlap" for signal in result.reasoning_record.signals)
    assert result.profile_graph.shared_nodes("enzyme")
    assert result.profile_graph.shared_nodes("target")


def test_safety_gate_flags_unsupported_quantitative_dose_guidance():
    def fake_rag_runner(*args, **kwargs):
        return {
            "context": {
                "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
                "signals": {"tabular": {}, "faers": {}, "mechanistic": {}},
                "pkpd": {"pk_detail": {}, "pd_detail": {}},
            },
            "answer": {"text": "Reduce the dose by 20-30% and continue therapy."},
        }

    result = AnalyzeMedicationSetUseCase(rag_runner=fake_rag_runner).execute(
        AnalyzeMedicationSetCommand(medications=["drug-a", "drug-b"])
    )

    assert result.safety_report.requires_review is True
    assert any(
        finding.finding_type == "unsupported_dose_guidance"
        for finding in result.safety_report.findings
    )
