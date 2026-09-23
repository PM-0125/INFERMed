import pytest

from src.application.interaction_modeling import (
    build_drug_profile_graph,
    build_interaction_reasoning_record,
)


@pytest.mark.parametrize(
    "overlaps",
    [None, [], 42, {}, {"a": [], "b": [], "c": []}, {"a": [None, "", " ", 42, False]}],
)
def test_empty_or_malformed_overlaps_do_not_create_hypotheses(overlaps):
    context = {"pkpd": {"pk_detail": {"overlaps": overlaps}}}
    graph = build_drug_profile_graph(
        analysis_id="test", drugs=["a", "b"], context=context, evidence_cards=[]
    )
    record = build_interaction_reasoning_record(
        analysis_id="test",
        drugs=["a", "b"],
        profile_graph=graph,
        context=context,
        evidence_cards=[],
    )
    assert not graph.shared_nodes("target")
    assert not record.hypotheses
    assert record.known_status == "unknown_insufficient_evidence"


def test_real_nested_overlaps_keep_their_biological_type():
    context = {
        "pkpd": {
            "pk_detail": {"overlaps": {"a": [["CYP3A4", "ABCB1"]]}},
            "pd_detail": {
                "overlap_targets": ["PTGS1"],
                "overlap_pathways": ["platelet activation"],
            },
        }
    }
    graph = build_drug_profile_graph(
        analysis_id="test", drugs=["a", "b"], context=context, evidence_cards=[]
    )
    assert [n.label for n in graph.shared_nodes("enzyme")] == ["CYP3A4"]
    assert [n.label for n in graph.shared_nodes("transporter")] == ["ABCB1"]
    assert [n.label for n in graph.shared_nodes("target")] == ["PTGS1"]
    assert [n.label for n in graph.shared_nodes("pathway")] == ["platelet activation"]


def test_pair_overlap_does_not_become_a_three_drug_claim():
    from src.domain.profile.entities import DrugProfileGraph, ProfileNode

    graph = DrugProfileGraph(
        analysis_id="test",
        drugs=["a", "b", "c"],
        nodes=[ProfileNode("enzyme:cyp", "enzyme", "CYP3A4", ["a", "b"])],
    )
    record = build_interaction_reasoning_record(
        analysis_id="test",
        drugs=["a", "b", "c"],
        profile_graph=graph,
        context={},
        evidence_cards=[],
    )
    assert record.hypotheses[0].affected_drugs == ["a", "b"]
