from src.application.commands import AnalyzeMedicationSetCommand
from src.application.use_cases.analyze_medication_set import AnalyzeMedicationSetUseCase


def test_patient_selected_passages_reach_reasoning_cards_not_only_context():
    units = [
        {
            "section": name,
            "text": text,
            "section_path": [name],
            "parent_context": [],
            "boundary_policy": "whole",
        }
        for name, text in [
            ("background_a", "Background observation. " * 210),
            ("background_b", "Other background fact. " * 220),
            ("renal_population", "Renal impairment study observation. " * 90),
        ]
    ]
    label = {
        "query": "alpha",
        "found": True,
        "sections": {},
        "source_units": units,
        "action_support_eligible": False,
    }

    def retrieve(*args, **kwargs):
        return {"signals": {"clinical_reference": {"dailymed": {"a": label}}}}

    def generate(ctx, *args, **kwargs):
        cards = ctx["reasoning_input"]["evidence"]
        daily = next(c for c in cards if c["source_name"] == "DailyMed SPL")
        assert any(
            p["section"] == "renal_population" for p in daily["payload"]["passages"]
        )
        selected = ctx["signals"]["clinical_reference"]["dailymed"]["a"]
        assert daily["payload"]["passages"] == selected["passages"]
        return {"text": "Source review requires clinical assessment."}

    AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=retrieve,
        answer_generator=generate,
    ).execute(
        AnalyzeMedicationSetCommand(
            medications=["alpha", "beta"],
            patient_context={"renal_function": "severe_impairment"},
        )
    )
