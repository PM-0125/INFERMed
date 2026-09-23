from src.application.source_tools import SourceToolInput, SourceToolResult
from src.domain.evidence.entities import EvidenceCard
from src.infrastructure.tools.executor import CachedToolExecutor
from src.infrastructure.persistence.cache_store import SQLiteToolCacheStore


def test_tool_cache_preserves_cards(tmp_path):
    card = EvidenceCard(
        "ev_1", "a", "ChEMBL", "api", "mechanism", "target X", "mechanistic", ["a"]
    )

    class Tool:
        def execute(self, request):
            return SourceToolResult(
                "test", "ChEMBL", True, evidence_cards=[card], raw_payload_hash="hash"
            )

    executor = CachedToolExecutor(SQLiteToolCacheStore(tmp_path / "cache.sqlite"))
    request = SourceToolInput("test", "ChEMBL", "mechanism", ["a"])
    first = executor.execute(Tool(), request)
    cached = executor.execute(Tool(), request)
    assert cached.evidence_cards == first.evidence_cards
    assert cached.raw_payload_hash == first.raw_payload_hash


def test_expired_tool_cache_is_not_reused(tmp_path):
    store = SQLiteToolCacheStore(tmp_path / "cache.sqlite")
    store.put(
        cache_key="expired",
        tool_name="test",
        source_name="source",
        input_payload={},
        payload={"old": True},
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert store.get("expired") is None


def test_cache_expiry_respects_timezone_offset(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = SQLiteToolCacheStore(tmp_path / "cache.sqlite")
    expired = datetime.now(timezone(timedelta(hours=5))) - timedelta(hours=1)
    store.put(
        cache_key="offset",
        tool_name="test",
        source_name="source",
        input_payload={},
        payload={},
        expires_at=expired.isoformat(),
    )
    assert store.get("offset") is None


def test_failed_enrichment_emits_failure_stage():
    from src.application.evidence_enrichment import (
        EnrichmentBudget,
        enrich_profile_gaps,
    )

    class Tool:
        def execute(self, request):
            raise TimeoutError()

    stages = []
    enrich_profile_gaps(
        requests=[SourceToolInput("test", "ChEMBL", "mechanism", ["a"])],
        available_tools={"test": Tool()},
        budget=EnrichmentBudget(),
        progress=lambda stage, *args: stages.append(stage),
    )
    assert stages[-1] == "evidence_enrichment_failed"


def test_enrichment_budget_deduplicates_and_stops():
    from src.application.evidence_enrichment import (
        EnrichmentBudget,
        enrich_profile_gaps,
    )

    calls = []

    class Tool:
        def execute(self, request):
            calls.append(request)
            return SourceToolResult("test", "ChEMBL", True)

    requests = [
        SourceToolInput("test", "ChEMBL", "mechanism", [str(i)]) for i in range(6)
    ]
    result = enrich_profile_gaps(
        requests=[requests[0], *requests],
        available_tools={"test": Tool()},
        budget=EnrichmentBudget(),
        clock=lambda: 0,
    )
    assert len(calls) == 4
    assert result.exhausted


def test_disabled_and_failed_tools_are_explicit():
    from src.application.evidence_enrichment import (
        EnrichmentBudget,
        enrich_profile_gaps,
    )

    result = enrich_profile_gaps(
        requests=[SourceToolInput("disabled", "ChEMBL", "mechanism", ["a"])],
        available_tools={},
        budget=EnrichmentBudget(),
    )
    assert not result.results
    assert result.skipped[0]["reason"] == "unavailable_or_disallowed"


def test_enrichment_deadline_prevents_calls():
    from src.application.evidence_enrichment import (
        EnrichmentBudget,
        enrich_profile_gaps,
    )

    result = enrich_profile_gaps(
        requests=[SourceToolInput("test", "ChEMBL", "mechanism", ["a"])],
        available_tools={"test": object()},
        budget=EnrichmentBudget(deadline_s=0),
    )
    assert not result.results
    assert result.exhausted


def test_exact_identifier_adapter_rejects_wrong_molecule(monkeypatch):
    from src.infrastructure.tools import graph_enrichment as module

    async def fetch(*args):
        return {
            "mechanisms": [
                {"molecule_chembl_id": "CHEMBL2", "target_chembl_id": "CHEMBL3"}
            ]
        }

    monkeypatch.setattr(module, "_fetch_json", fetch)
    tool = module.ExactGraphTool("fetch_chembl_bioactivity", "ChEMBL")
    result = tool.execute(
        SourceToolInput(
            tool.name,
            "ChEMBL",
            "graph_mechanism",
            ["a"],
            {"identifier": "CHEMBL1", "timeout_s": 1},
        )
    )
    assert not result.evidence_cards


def test_enrichment_reaches_final_profile_and_generation(tmp_path, monkeypatch):
    from src.infrastructure.tools import graph_enrichment as module
    from src.application.use_cases.analyze_medication_set import (
        AnalyzeMedicationSetUseCase,
    )
    from src.application.commands import AnalyzeMedicationSetCommand
    from src.config.settings import Settings
    from src.infrastructure.persistence.event_store import SQLiteEventStore

    async def fetch(*args):
        return {
            "mechanisms": [
                {
                    "molecule_chembl_id": "CHEMBL1",
                    "target_chembl_id": "CHEMBL3",
                    "mechanism_of_action": "Synthetic fixture",
                }
            ]
        }

    monkeypatch.setattr(module, "_fetch_json", fetch)

    def context(a, b, **kwargs):
        return {"drugs": {"a": {"name": a, "chembl_id": "CHEMBL1"}, "b": {"name": b}}}

    def generator(ctx, *args, **kwargs):
        cards = ctx["reasoning_input"]["evidence"]
        assert any(c["claim_type"] == "mechanism_enrichment" for c in cards)
        assert any(
            n["label"] == "CHEMBL3" and n["drug_scope"] == ["a"]
            for n in ctx["reasoning_input"]["profiles"]["nodes"]
        )
        return {"text": "Limited evidence; clinical interaction remains uncertain."}

    result = AnalyzeMedicationSetUseCase(
        rag_runner=lambda *a, **k: {},
        context_runner=context,
        answer_generator=generator,
        evidence_enricher=module.enrich_pair_results,
        settings=Settings(sqlite_cache_path=str(tmp_path / "cache.sqlite")),
        event_store=SQLiteEventStore(tmp_path / "events.sqlite"),
    ).execute(AnalyzeMedicationSetCommand(medications=["a", "b"]))
    assert not result.profile_graph.shared_nodes("target")
