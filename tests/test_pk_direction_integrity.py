import pytest

from src.domain.mechanism.engines.pk import detect_pk_mechanisms
from src.domain.decision.scoring import score_pair_context
from src.application.use_cases.analyze_medication_set import decision_from_context
from src.application.use_cases.analyze_medication_set import evidence_cards_from_context


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("role", ["inhibitor", "inducer"])
def test_pk_graph_direction_follows_roles_independent_of_input_order(reverse, role):
    pair = ["actor", "substrate"]
    roles = [{role: ["CYP3A4"]}, {"substrate": ["CYP3A4"]}]
    if reverse:
        pair.reverse()
        roles.reverse()
    context = {"signals": {"mechanistic": {"enzymes": {"a": roles[0], "b": roles[1]}}}}
    graph = detect_pk_mechanisms(context, [], pair)
    assert len(graph.clusters) == 1
    assert graph.clusters[0].drivers == ["actor"]
    assert graph.clusters[0].affected_drugs == ["substrate"]
    assert any(
        edge.source == "drug:actor" and edge.type == "drives" for edge in graph.edges
    )
    assert any(
        edge.target == "drug:substrate" and edge.type == "affects"
        for edge in graph.edges
    )
    assert graph.nodes[0].payload["clinical_consequence"] == "not established"


def test_shared_protein_does_not_invent_a_pk_perpetrator():
    graph = detect_pk_mechanisms(
        {"pkpd": {"pk_detail": {"overlaps": {"shared": ["CYP3A4"]}}}}, [], ["a", "b"]
    )
    assert graph.nodes
    assert not any(edge.type in {"drives", "affects"} for edge in graph.edges)
    assert all(not cluster.drivers for cluster in graph.clusters)


def test_negative_summary_and_empty_overlaps_do_not_create_a_pk_mechanism():
    graph = detect_pk_mechanisms(
        {
            "pkpd": {
                "pk_summary": "No strong PK overlap detected",
                "pk_detail": {"overlaps": {"a": [], "b": [], "c": []}},
            }
        },
        [],
        ["a", "b"],
    )
    assert not graph.nodes
    assert not graph.clusters


def test_canonical_evidence_is_retained_without_guessing_direction():
    graph = detect_pk_mechanisms(
        {
            "pkpd": {
                "pk_detail": {
                    "canonical_interaction": {
                        "mechanism": "A source-described mechanism"
                    }
                }
            }
        },
        [],
        ["a", "b"],
    )
    assert (
        graph.nodes[0].payload["canonical_interaction"]["mechanism"]
        == "A source-described mechanism"
    )
    assert not any(edge.type in {"drives", "affects"} for edge in graph.edges)


def test_role_chain_cites_specific_role_records_not_a_conflicting_summary():
    context = {
        "signals": {
            "mechanistic": {
                "enzymes": {
                    "a": {"inhibitor": ["CYP3A4"]},
                    "b": {"substrate": ["CYP3A4"]},
                }
            }
        },
        "pkpd": {"pk_summary": "No strong PK overlap detected"},
    }
    cards = evidence_cards_from_context("test", context, ["actor", "substrate"])
    graph = detect_pk_mechanisms(context, cards, ["actor", "substrate"])
    by_id = {card.evidence_id: card for card in cards}
    actor_edge = next(edge for edge in graph.edges if edge.type == "drives")
    affected_edge = next(edge for edge in graph.edges if edge.type == "affects")
    assert len(actor_edge.evidence_ids) == len(affected_edge.evidence_ids) == 1
    actor_card = by_id[actor_edge.evidence_ids[0]]
    affected_card = by_id[affected_edge.evidence_ids[0]]
    assert actor_card.drug_scope == ["actor"]
    assert actor_card.payload["role"] == "inhibitor"
    assert actor_card.payload["protein"] == "CYP3A4"
    assert affected_card.drug_scope == ["substrate"]
    assert affected_card.payload["role"] == "substrate"
    assert all(
        by_id[eid].claim_type == "enzyme_role"
        for edge in graph.edges
        for eid in edge.evidence_ids
    )


@pytest.mark.parametrize("pair", [["actor", "affected"], ["affected", "actor"]])
def test_explicit_canonical_direction_is_preserved(pair):
    context = {
        "pkpd": {
            "pk_detail": {
                "canonical_interaction": {
                    "drug_a": "actor",
                    "drug_b": "affected",
                    "primary_affected_drug": "affected",
                    "mechanism": "Source-described mechanism",
                    "direction": "increase",
                }
            }
        }
    }
    cards = evidence_cards_from_context("test", context, pair)
    graph = detect_pk_mechanisms(context, cards, pair)
    assert graph.clusters[0].drivers == ["actor"]
    assert graph.clusters[0].affected_drugs == ["affected"]
    assert (
        graph.nodes[0].payload["knowledge_basis"] == "retrieved_canonical_interaction"
    )


@pytest.mark.parametrize("extra", [{}, {"overlaps": {"shared": ["CYP3A4"]}}])
def test_unspecified_canonical_severity_does_not_create_a_clinical_risk_grade(extra):
    context = {
        "pkpd": {
            "pk_detail": {
                "canonical_interaction": {
                    "mechanism": "Retrieved mechanism with no severity"
                },
                **extra,
            }
        },
        "signals": {"tabular": {"prr": 100}},
    }
    assert score_pair_context(context)[:2] == ("insufficient_evidence", "low")
    decision = decision_from_context("test", context, [], ["a", "b"])
    assert "No usable evidence" not in decision.abstention_reason
