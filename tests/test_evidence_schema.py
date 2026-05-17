from dataclasses import asdict

from src.core.evidence import EvidenceBundle, EvidenceItem, SourceStatus


def test_evidence_schema_preserves_source_trace():
    item = EvidenceItem(
        source="TWOSIDES",
        evidence_type="pair_adverse_event_signal",
        subject="warfarin + ibuprofen",
        predicate="reported_pair_side_effect",
        object="bleeding",
        confidence="associative",
        raw={"prr": 2.4},
    )
    status = SourceStatus("TWOSIDES", enabled=True, available=True, reason="registered")
    bundle = EvidenceBundle("warfarin", "ibuprofen", [item], [status], ["associative signal only"])

    assert asdict(bundle)["items"][0]["source"] == "TWOSIDES"
    assert bundle.items[0].visibility == "public"
    assert bundle.source_status[0].available is True
