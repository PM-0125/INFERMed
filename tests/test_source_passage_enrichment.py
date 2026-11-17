from src.retrieval.dailymed_client import DailyMedClient


def test_identical_openfda_version_alias_deduplicates():
    daily = DailyMedClient.parse_spl(
        XML,
        "alpha",
        {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"},
    )
    old = {**daily, "version": "3"}
    old.pop("spl_version")
    cards = evidence_cards_from_context(
        "test",
        {
            "signals": {
                "clinical_reference": {
                    "dailymed": {"a": daily},
                    "openfda_label": {"a": old},
                }
            }
        },
        ["alpha", "beta"],
    )
    assert len([c for c in cards if c.claim_type == "label_context"]) == 1


from src.retrieval.research_api_clients import EuropePMCClient
from src.domain.explanation.passages import source_passages
from src.application.use_cases.analyze_medication_set import evidence_cards_from_context

XML = """<document xmlns="urn:hl7-org:v3"><setId root="11111111-1111-1111-1111-111111111111"/><versionNumber value="3"/><code code="34391-3"/>
<ingredient classCode="ACTIB"><ingredientSubstance><name>ALPHA</name></ingredientSubstance></ingredient>
<component><section><code code="34073-7"/><title>Drug interactions</title><text><paragraph>Alpha increases beta exposure only in the studied population.</paragraph><paragraph>Do not extrapolate this result.</paragraph></text></section></component></document>"""


def test_spl_preserves_qualifiers_and_version_and_rejects_wrong_drug():
    record = {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"}
    result = DailyMedClient.parse_spl(XML, "alpha", record)
    assert result["sections"]["drug_interactions"].endswith(
        "Do not extrapolate this result."
    )
    assert result["spl_version"] == "3"
    assert not DailyMedClient.parse_spl(XML, "gamma", record)["found"]
    assert not DailyMedClient.parse_spl(XML, "alpha", {**record, "spl_version": "4"})[
        "found"
    ]


def test_daily_label_preserves_product_uncertainty_and_cannot_authorize_actions():
    result = DailyMedClient.parse_spl(
        XML,
        "alpha",
        {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"},
    )
    cards = evidence_cards_from_context(
        "test",
        {"signals": {"clinical_reference": {"dailymed": {"a": result}}}},
        ["alpha", "beta"],
    )
    card = next(c for c in cards if c.source_name == "DailyMed SPL")
    assert source_passages(card)
    assert source_passages(card, actions=True) == []


def test_newer_label_is_not_discarded_under_same_setid():
    daily = DailyMedClient.parse_spl(
        XML,
        "alpha",
        {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"},
    )
    old = {**daily, "spl_version": "2", "sections": {"warnings": "Older warning only."}}
    cards = evidence_cards_from_context(
        "test",
        {
            "signals": {
                "clinical_reference": {
                    "dailymed": {"a": daily},
                    "openfda_label": {"a": old},
                }
            }
        },
        ["alpha", "beta"],
    )
    assert any(
        c.source_name == "DailyMed SPL" and c.payload["spl_version"] == "3"
        for c in cards
    )


def test_abstract_retained_without_promoting_it_to_guideline(monkeypatch, tmp_path):
    client = EuropePMCClient(cache_dir=str(tmp_path))

    class Response:
        status_code = 200

        def json(self):
            return {
                "resultList": {
                    "result": [
                        {
                            "title": "Alpha beta trial",
                            "pmid": "123",
                            "abstractText": "<p>Alpha increased beta exposure.</p><p>Only healthy volunteers were studied.</p>",
                            "pubTypeList": {"pubType": ["Clinical Trial"]},
                            "isOpenAccess": "Y",
                        }
                    ]
                }
            }

    monkeypatch.setattr(client._session, "request", lambda *a, **k: Response())
    article = client.search_interaction_literature("alpha", "beta")["articles"][0]
    assert "Only healthy volunteers were studied." in article["abstract"]
    context = {
        "signals": {"research_enrichment": {"europe_pmc": {"articles": [article]}}}
    }
    cards = evidence_cards_from_context("test", context, ["alpha", "beta"])
    card = next(c for c in cards if c.source_name == "Europe PMC")
    assert source_passages(card)
    assert source_passages(card, actions=True) == []
