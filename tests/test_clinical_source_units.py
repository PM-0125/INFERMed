from src.retrieval.dailymed_client import DailyMedClient
from src.domain.evidence.label_passages import select_label_passages
from src.application.source_selection import passage_catalog
from src.domain.evidence.entities import EvidenceCard


def nested_label():
    xml = """<document xmlns="urn:hl7-org:v3"><setId root="11111111-1111-1111-1111-111111111111"/><versionNumber value="3"/><code code="34391-3"/>
    <ingredient classCode="ACTIB"><ingredientSubstance><name>ALPHA</name></ingredientSubstance></ingredient>
    <component><section><code code="34073-7"/><title>Drug interactions</title><text><paragraph>Only in patients with severe renal impairment:</paragraph></text>
    <component><section><code code="42229-5"/><title>Beta</title><text><paragraph>Alpha increases beta exposure.</paragraph></text></section></component>
    <component><section><code code="42229-5"/><title>Gamma</title><text><paragraph>Gamma exposure is unchanged.</paragraph></text></section></component>
    </section></component></document>"""
    return DailyMedClient.parse_spl(
        xml,
        "alpha",
        {"set_id": "11111111-1111-1111-1111-111111111111", "spl_version": "3"},
    )


def test_nested_unit_retains_parent_condition_without_sibling_drug():
    label = nested_label()
    unit = next(u for u in label["source_units"] if u["section_path"][-1] == "Beta")
    assert "Only in patients with severe renal impairment:" in unit["text"]
    assert "Alpha increases beta exposure." in unit["text"]
    assert "Gamma" not in unit["text"]
    assert unit["parent_context"] == ["Only in patients with severe renal impairment:"]


def test_structural_unit_survives_selection_with_stable_identity():
    selected = select_label_passages(nested_label(), ["alpha", "beta"])
    card = EvidenceCard(
        "ev_one",
        "one",
        "DailyMed SPL",
        "public_label_api",
        "label_context",
        "label",
        "label",
        ["alpha"],
        {},
        selected,
        [],
    )
    graph = {"decision": {"drugs": ["alpha", "beta"]}, "evidence": [card.to_dict()]}
    entries = passage_catalog(graph)
    beta = next(p for p in entries if "beta exposure" in p["text"])
    assert "Only in patients" in beta["text"]
    assert not beta["action_support_eligible"]
    graph["evidence"][0]["evidence_id"] = "ev_two"
    changed = next(p for p in passage_catalog(graph) if "beta exposure" in p["text"])
    assert changed["content_id"] == beta["content_id"]
    assert changed["evidence_id"] != beta["evidence_id"]


def test_condition_heading_is_part_of_bound_text_and_applicability():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        '<text xmlns="urn:hl7-org:v3"><paragraph>Alpha increases beta exposure.</paragraph></text>'
    )
    units = split_text_units(
        body, ["Only in patients with severe renal impairment"], []
    )
    assert "Only in patients with severe renal impairment" in units[0]["text"]


def test_explicit_inline_headings_keep_intro_and_exclude_sibling():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text xmlns="urn:hl7-org:v3"><paragraph>Only for the studied population.</paragraph>
    <paragraph><content styleCode="italics">Beta:</content>Alpha increases beta exposure.
    <content styleCode="italics"><br/><br/>Gamma:</content>Gamma exposure is unchanged.</paragraph></text>"""
    )
    units = split_text_units(
        body, ["Interaction studies"], [], peer_headings={"Beta", "Gamma"}
    )
    beta = next(u for u in units if u["section_path"][-1] == "Beta:")
    assert "Only for the studied population." in beta["text"]
    assert "Alpha increases beta exposure." in beta["text"]
    assert "Gamma" not in beta["text"]


def test_styled_condition_heading_cannot_be_discarded_as_a_sibling():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text xmlns="urn:hl7-org:v3"><paragraph><content styleCode="bold">Only in patients with severe renal impairment:</content></paragraph>
    <paragraph><content styleCode="italics">Beta:</content>Alpha increases beta exposure.</paragraph></text>"""
    )
    units = split_text_units(body, ["Drug interactions"], [])
    assert all(
        "Only in patients with severe renal impairment:" in u["text"]
        for u in units
        if "beta exposure" in u["text"]
    )


def test_unrecognized_same_style_headings_keep_entire_governing_context():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    for heading in ("Intravenous administration", "Women", "CrCl below 30 mL/min"):
        body = ET.fromstring(
            f"""<text><paragraph><content styleCode="italics">{heading}:</content>Restricted population.</paragraph>
        <paragraph><content styleCode="italics">Beta:</content>Alpha increases beta exposure.</paragraph></text>"""
        )
        units = split_text_units(
            body, ["Drug interactions"], [], peer_headings={"Beta"}
        )
        assert len(units) == 1
        assert heading in units[0]["text"]
        assert "Restricted population." in units[0]["text"]


def test_unverified_styling_never_establishes_peer_boundaries():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text><paragraph><content styleCode="italics">Alpha:</content>Context.</paragraph>
    <paragraph><content styleCode="italics">Beta:</content>Observation.</paragraph></text>"""
    )
    assert len(split_text_units(body, ["Drug interactions"], [])) == 1


def test_numbered_subsections_preserve_governing_parent_and_qualifiers():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text><paragraph>For oral use only.</paragraph>
    <paragraph><content styleCode="bold">7.1 Renal impairment</content>Only when measured clearance is below 30.</paragraph>
    <paragraph><content styleCode="bold">7.1.1 Alpha</content>Alpha increases beta exposure. The effect was not studied in dialysis.</paragraph>
    <paragraph><content styleCode="bold">7.2 Gamma</content>Gamma is a separate observation.</paragraph></text>"""
    )
    units = split_text_units(body, ["7 Interactions"], [])
    alpha = next(u for u in units if u["section_path"][-1] == "7.1.1 Alpha")
    assert "For oral use only." in alpha["text"]
    assert "Only when measured clearance is below 30." in alpha["text"]
    assert "not studied in dialysis" in alpha["text"]
    assert "Gamma" not in alpha["text"]


def test_unknown_heading_among_numbered_subsections_prevents_split():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text><paragraph><content styleCode="bold">Women:</content>Specific restriction.</paragraph>
    <paragraph><content styleCode="bold">7.1 Alpha</content>Alpha increases beta exposure.</paragraph></text>"""
    )
    units = split_text_units(body, ["Interactions"], [])
    assert len(units) == 1
    assert "Specific restriction" in units[0]["text"]


def test_unnumbered_population_heading_between_numbered_peers_prevents_split():
    from src.retrieval.spl_units import split_text_units
    import xml.etree.ElementTree as ET

    body = ET.fromstring(
        """<text><paragraph><content styleCode="bold">7.1 Alpha</content>Alpha observation.</paragraph>
    <paragraph><content styleCode="bold">Women</content>Specific restriction.</paragraph>
    <paragraph><content styleCode="bold">7.2 Beta</content>Beta observation.</paragraph></text>"""
    )
    assert len(split_text_units(body, ["Interactions"], [])) == 1
