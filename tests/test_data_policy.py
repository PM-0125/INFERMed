from src.config.data_policy import get_source_status
from src.config.settings import get_settings


def test_public_safe_forces_restricted_sources_off(monkeypatch):
    monkeypatch.setenv("INFERMED_DATA_MODE", "public_safe")
    monkeypatch.setenv("ENABLE_DRUGBANK", "true")
    monkeypatch.setenv("ENABLE_QLEVER", "true")

    settings = get_settings()

    assert settings.enable_drugbank is False
    assert settings.enable_qlever is False

    statuses = {s.name: s for s in get_source_status(settings)}
    assert statuses["DrugBank local dataset"].enabled is False
    assert statuses["QLever RDF"].enabled is False
    assert "Disabled" in statuses["QLever RDF"].reason


def test_required_public_enrichments_are_not_optional(monkeypatch):
    monkeypatch.setenv("INFERMED_DATA_MODE", "public_safe")
    monkeypatch.setenv("ENABLE_CHEMBL", "false")
    monkeypatch.setenv("ENABLE_KEGG", "false")
    monkeypatch.setenv("ENABLE_REACTOME", "false")
    monkeypatch.setenv("ENABLE_UNIPROT", "false")

    settings = get_settings()

    assert settings.enable_pubchem_rest is True
    assert settings.enable_pubchem_pugview is True
    assert settings.enable_chembl is True
    assert settings.enable_kegg is True
    assert settings.enable_reactome is True
    assert settings.enable_uniprot is True

    statuses = {s.name: s for s in get_source_status(settings)}
    for source_name in ("PubChem PUG-REST", "PubChem PUG-View", "ChEMBL", "KEGG", "Reactome", "UniProt"):
        assert statuses[source_name].enabled is True
        assert statuses[source_name].reason == "Required public enrichment"
