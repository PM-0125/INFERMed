from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

DataMode = Literal["public_safe", "local_dev", "full_research_future"]
ToolCategory = Literal["retrieval", "mechanism", "clinical_context", "model"]
ToolVisibility = Literal["public", "restricted_local", "credentialed"]


@dataclass(frozen=True)
class DataSourcePolicy:
    source_name: str
    access: str
    allowed_modes: tuple[DataMode, ...]
    can_commit_raw: bool
    can_cache_normalized: bool
    requires_attribution: bool
    requires_license_key_or_dua: bool
    clinical_caveat: str = ""

    def allows_mode(self, data_mode: str) -> bool:
        return data_mode in self.allowed_modes

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    category: ToolCategory
    source_name: str
    visibility: ToolVisibility
    allowed_modes: tuple[DataMode, ...]
    requires_license: bool = False
    may_run_in_public: bool = True
    version: str = "1"

    def allows_mode(self, data_mode: str) -> bool:
        if data_mode == "public_safe" and (self.requires_license or not self.may_run_in_public):
            return False
        return data_mode in self.allowed_modes

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition], policies: list[DataSourcePolicy]):
        self._tools = {tool.name: tool for tool in tools}
        self._policies = {policy.source_name: policy for policy in policies}

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def allowed_tools(self, data_mode: str) -> list[ToolDefinition]:
        out: list[ToolDefinition] = []
        for tool in self._tools.values():
            policy = self._policies.get(tool.source_name)
            if not tool.allows_mode(data_mode):
                continue
            if policy is not None and not policy.allows_mode(data_mode):
                continue
            out.append(tool)
        return sorted(out, key=lambda item: item.name)

    def skipped_tools(self, data_mode: str) -> list[ToolDefinition]:
        allowed = {tool.name for tool in self.allowed_tools(data_mode)}
        return sorted([tool for tool in self._tools.values() if tool.name not in allowed], key=lambda item: item.name)

    def policy_for(self, source_name: str) -> DataSourcePolicy | None:
        return self._policies.get(source_name)


def default_tool_registry() -> ToolRegistry:
    public_modes: tuple[DataMode, ...] = ("public_safe", "local_dev", "full_research_future")
    local_modes: tuple[DataMode, ...] = ("local_dev", "full_research_future")

    policies = [
        DataSourcePolicy("OpenFDA FAERS", "public_api", public_modes, False, True, True, False, "Associative reports only."),
        DataSourcePolicy("openFDA Drug Label", "public_api", public_modes, False, True, True, False, "SPL-derived labels should remain product/version/date specific."),
        DataSourcePolicy("DailyMed SPL", "public_api", public_modes, False, True, True, False, "Highest-authority US label source when available."),
        DataSourcePolicy("PubChem", "public_api", public_modes, False, True, True, False, "Compound identity and structure source."),
        DataSourcePolicy("ChEMBL", "public_api", public_modes, False, True, True, False, "Bioactivity evidence requires source attribution."),
        DataSourcePolicy("UniProt", "public_api", public_modes, False, True, True, False, "Protein metadata requires source attribution."),
        DataSourcePolicy("KEGG", "public_api", public_modes, False, True, True, False, "Use with license caution for non-academic/commercial service use."),
        DataSourcePolicy("Reactome", "public_api", public_modes, False, True, True, False, "Pathway data source."),
        DataSourcePolicy("FDA PGx", "public_page", public_modes, False, True, True, False, "Pharmacogenomic label/context reference; not a standalone DDI rule."),
        DataSourcePolicy("Europe PMC", "public_api", public_modes, False, True, True, False, "Literature metadata/discovery context, not article-level proof by itself."),
        DataSourcePolicy("Open Targets", "public_api", public_modes, False, True, True, False, "Target/disease association context."),
        DataSourcePolicy("STRING", "public_api", public_modes, False, True, True, False, "Protein network context; confidence scores are network evidence."),
        DataSourcePolicy("BioGRID", "credentialed_public_api", public_modes, False, True, True, False, "Credential-gated interaction evidence; respect API key terms."),
        DataSourcePolicy("DrugCentral", "public_api", public_modes, False, True, True, False, "Drug mechanism/indication/safety context."),
        DataSourcePolicy("NCI ALMANAC", "local_file", local_modes, False, True, True, False, "Cancer-cell-line combination-response research context only."),
        DataSourcePolicy("SIDER", "local_file", public_modes, False, True, True, False, "Side-effect context; not patient-specific incidence."),
        DataSourcePolicy("nSIDES", "local_file", public_modes, False, True, True, False, "Predicted/observational side-effect context; hypothesis support only."),
        DataSourcePolicy("OFFSIDES", "local_file", public_modes, False, True, True, False, "Off-label side-effect signal context; hypothesis support only."),
        DataSourcePolicy("Local TWOSIDES", "local_file", public_modes, False, True, True, False, "Signal context only; not incidence or causality."),
        DataSourcePolicy("Local DILIrank", "local_file", public_modes, False, True, True, False, "Toxicity propensity context."),
        DataSourcePolicy("Local DICTRank", "local_file", public_modes, False, True, True, False, "Cardiotoxicity score context."),
        DataSourcePolicy("Local DIQT", "local_file", public_modes, False, True, True, False, "QT-prolongation score context."),
        DataSourcePolicy("Canonical PK/PD", "project_dictionary", public_modes, True, True, False, False, "Project-authored mechanism seed layer."),
        DataSourcePolicy("DrugBank", "licensed_local", local_modes, False, True, True, True, "Restricted licensed source; never public-safe."),
    ]

    tools = [
        ToolDefinition("fetch_openfda_faers", "retrieval", "OpenFDA FAERS", "public", public_modes),
        ToolDefinition("fetch_openfda_label", "retrieval", "openFDA Drug Label", "public", public_modes),
        ToolDefinition("fetch_dailymed_label", "retrieval", "DailyMed SPL", "public", public_modes),
        ToolDefinition("fetch_pubchem_compound", "retrieval", "PubChem", "public", public_modes),
        ToolDefinition("fetch_chembl_bioactivity", "retrieval", "ChEMBL", "public", public_modes),
        ToolDefinition("fetch_uniprot_targets", "retrieval", "UniProt", "public", public_modes),
        ToolDefinition("fetch_kegg_pathways", "retrieval", "KEGG", "public", public_modes),
        ToolDefinition("fetch_reactome_pathways", "retrieval", "Reactome", "public", public_modes),
        ToolDefinition("fetch_fda_pgx_context", "retrieval", "FDA PGx", "public", public_modes),
        ToolDefinition("fetch_europe_pmc_metadata", "retrieval", "Europe PMC", "public", public_modes),
        ToolDefinition("fetch_open_targets_context", "retrieval", "Open Targets", "public", public_modes),
        ToolDefinition("fetch_string_network", "retrieval", "STRING", "public", public_modes),
        ToolDefinition("fetch_biogrid_interactions", "retrieval", "BioGRID", "credentialed", public_modes),
        ToolDefinition("fetch_drugcentral_context", "retrieval", "DrugCentral", "public", public_modes),
        ToolDefinition("query_nci_almanac_local", "retrieval", "NCI ALMANAC", "public", local_modes),
        ToolDefinition("query_sider_local", "retrieval", "SIDER", "public", public_modes),
        ToolDefinition("query_nsides_local", "retrieval", "nSIDES", "public", public_modes),
        ToolDefinition("query_offsides_local", "retrieval", "OFFSIDES", "public", public_modes),
        ToolDefinition("query_local_twosides", "retrieval", "Local TWOSIDES", "public", public_modes),
        ToolDefinition("query_local_dilirank", "retrieval", "Local DILIrank", "public", public_modes),
        ToolDefinition("query_local_dictrank", "retrieval", "Local DICTRank", "public", public_modes),
        ToolDefinition("query_local_diqt", "retrieval", "Local DIQT", "public", public_modes),
        ToolDefinition("query_canonical_pkpd", "retrieval", "Canonical PK/PD", "public", public_modes),
        ToolDefinition("query_drugbank_local", "retrieval", "DrugBank", "restricted_local", local_modes, requires_license=True, may_run_in_public=False),
        ToolDefinition("detect_cyp_pk_overlap", "mechanism", "Canonical PK/PD", "public", public_modes),
        ToolDefinition("detect_qt_burden", "mechanism", "Local DIQT", "public", public_modes),
        ToolDefinition("detect_hepatotoxicity_burden", "mechanism", "Local DILIrank", "public", public_modes),
        ToolDefinition("build_ndrug_mechanism_graph", "mechanism", "Canonical PK/PD", "public", public_modes),
        ToolDefinition("draft_explanation", "model", "Canonical PK/PD", "public", public_modes),
        ToolDefinition("verify_explanation_grounding", "model", "Canonical PK/PD", "public", public_modes),
    ]
    return ToolRegistry(tools, policies)
