from src.infrastructure.tools.registry import default_tool_registry


def test_tool_registry_blocks_drugbank_in_public_safe():
    registry = default_tool_registry()

    allowed = {tool.name for tool in registry.allowed_tools("public_safe")}
    skipped = {tool.name for tool in registry.skipped_tools("public_safe")}

    assert "query_local_twosides" in allowed
    assert "fetch_openfda_faers" in allowed
    assert "query_drugbank_local" not in allowed
    assert "query_drugbank_local" in skipped


def test_tool_registry_allows_drugbank_in_local_dev():
    registry = default_tool_registry()

    allowed = {tool.name for tool in registry.allowed_tools("local_dev")}

    assert "query_drugbank_local" in allowed

