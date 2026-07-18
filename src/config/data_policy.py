from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.config.settings import Settings, get_settings
from src.core.evidence import SourceStatus


_PUBLIC_DUCKDB_DATASETS = {
    "TWOSIDES": "twosides.parquet",
    "DILIrank": "dilirank.parquet",
    "DICTRank": "dictrank.parquet",
    "DIQT": "diqt.parquet",
}


def _file_status(name: str, enabled: bool, path: Path) -> SourceStatus:
    if not enabled:
        return SourceStatus(name=name, enabled=False, available=False, reason="Disabled by config")
    if path.exists():
        return SourceStatus(name=name, enabled=True, available=True, reason="Local file present")
    return SourceStatus(name=name, enabled=True, available=False, reason=f"Missing local file: {path.name}")


def _load_manifest_datasets(settings: Settings) -> dict[str, Any]:
    manifest_path = Path(settings.data_manifest_path)
    if not manifest_path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    datasets = payload.get("datasets", {})
    return datasets if isinstance(datasets, dict) else {}


def _dataset_enabled(dataset_name: str, dataset: dict[str, Any], settings: Settings) -> bool:
    if not settings.enable_duckdb:
        return False
    if dataset_name == "drugbank" or dataset.get("visibility") == "restricted_local":
        return settings.data_mode != "public_safe" and settings.enable_drugbank
    if settings.data_mode == "public_safe" and dataset.get("enabled_in_public_safe") is False:
        return False
    return True


def _duckdb_statuses_from_manifest(settings: Settings) -> list[SourceStatus]:
    datasets = _load_manifest_datasets(settings)
    if not datasets:
        return []

    statuses: list[SourceStatus] = []
    for dataset_name, dataset in datasets.items():
        if not isinstance(dataset, dict) or dataset.get("backend") != "duckdb":
            continue
        label = str(dataset.get("source_label") or dataset_name)
        file_value = dataset.get("file")
        if not file_value:
            statuses.append(SourceStatus(label, enabled=False, available=False, reason="Missing file path in manifest"))
            continue
        enabled = _dataset_enabled(str(dataset_name), dataset, settings)
        reason_disabled = "Disabled in public_safe mode" if str(dataset_name) == "drugbank" and settings.data_mode == "public_safe" else "Disabled by config"
        status = _file_status(label, enabled, Path(str(file_value)))
        if not enabled:
            status = SourceStatus(label, enabled=False, available=False, reason=reason_disabled)
        statuses.append(status)
    return statuses


def get_source_status(settings: Settings | None = None) -> list[SourceStatus]:
    settings = settings or get_settings()
    duckdb_dir = Path(settings.duckdb_dir)

    statuses: list[SourceStatus] = _duckdb_statuses_from_manifest(settings)
    if not statuses:
        for source_name, filename in _PUBLIC_DUCKDB_DATASETS.items():
            statuses.append(_file_status(source_name, settings.enable_duckdb, duckdb_dir / filename))

        if settings.data_mode == "public_safe":
            statuses.append(
                SourceStatus(
                    name="DrugBank local dataset",
                    enabled=False,
                    available=False,
                    reason="Disabled in public_safe mode",
                )
            )
        else:
            statuses.append(
                _file_status(
                    "DrugBank local dataset",
                    settings.enable_duckdb and settings.enable_drugbank,
                    duckdb_dir / "drugbank.parquet",
                )
            )

    qlever_available = bool(os.getenv("CORE_ENDPOINT") and os.getenv("DISEASE_ENDPOINT"))
    statuses.append(
        SourceStatus(
            name="QLever RDF",
            enabled=settings.enable_qlever,
            available=settings.enable_qlever and qlever_available,
            reason=(
                "Disabled for NVIDIA demo runtime"
                if not settings.enable_qlever
                else "CORE_ENDPOINT and DISEASE_ENDPOINT configured" if qlever_available
                else "Missing CORE_ENDPOINT or DISEASE_ENDPOINT"
            ),
        )
    )

    statuses.extend(
        [
            SourceStatus("OpenFDA cache/API", settings.enable_openfda, settings.enable_openfda, "Availability checked per request"),
            SourceStatus("openFDA Drug Label API", settings.enable_openfda_label, settings.enable_openfda_label, "Public SPL-derived label sections"),
            SourceStatus("DailyMed SPL API", settings.enable_dailymed, settings.enable_dailymed, "Public SPL metadata"),
            SourceStatus("RxNorm/RxClass API", settings.enable_rxnorm, settings.enable_rxnorm, "Public NLM medication identity and class normalization"),
            SourceStatus(
                "FDA CYP/transporter reference",
                True,
                Path("data/reference/fda_ddi_tables.json").exists(),
                "Downloaded with scripts/download_public_sources.py",
            ),
            SourceStatus(
                "PubChem PUG-REST",
                settings.enable_pubchem_rest,
                settings.enable_pubchem_rest,
                "Required public enrichment",
            ),
            SourceStatus(
                "PubChem PUG-View",
                settings.enable_pubchem_pugview,
                settings.enable_pubchem_pugview,
                "Required public enrichment",
            ),
            SourceStatus("ChEMBL", settings.enable_chembl, settings.enable_chembl, "Required public enrichment"),
            SourceStatus("KEGG", settings.enable_kegg, settings.enable_kegg, "Required public enrichment"),
            SourceStatus("Reactome", settings.enable_reactome, settings.enable_reactome, "Required public enrichment"),
            SourceStatus("UniProt", settings.enable_uniprot, settings.enable_uniprot, "Required public enrichment"),
            SourceStatus("FDA PGx biomarker pages", settings.enable_fda_pgx, settings.enable_fda_pgx, "Public FDA page lookup; matched per request"),
            SourceStatus("Europe PMC REST API", settings.enable_europe_pmc, settings.enable_europe_pmc, "Public literature metadata search"),
            SourceStatus("Open Targets GraphQL API", settings.enable_open_targets, settings.enable_open_targets, "Public target-disease/drug search; best-effort per request"),
            SourceStatus("STRING API", settings.enable_stringdb, settings.enable_stringdb, "Public protein association lookup with rate limiting"),
            SourceStatus(
                "BioGRID REST API",
                settings.enable_biogrid,
                settings.enable_biogrid and bool(settings.biogrid_access_key),
                "BIOGRID_ACCESS_KEY configured" if settings.biogrid_access_key else "Requires BIOGRID_ACCESS_KEY",
            ),
            SourceStatus("DrugCentral API", settings.enable_drugcentral, settings.enable_drugcentral, "Public structure and target/activity lookup"),
            SourceStatus(
                "NCI-ALMANAC raw rebuild input",
                settings.enable_nci_almanac,
                Path("data/raw/nci_almanac/ComboDrugGrowth_Nov2017.zip").exists(),
                "Downloaded raw rebuild input" if Path("data/raw/nci_almanac/ComboDrugGrowth_Nov2017.zip").exists() else "Bulk/local research source; run scripts/download_research_sources.py",
            ),
            SourceStatus(
                "SIDER/nSIDES/OFFSIDES",
                settings.enable_sider_nsides_offsides,
                Path("data/raw/sider/meddra_all_se.tsv.gz").exists() or Path("data/raw/nsides/OFFSIDES.csv.xz").exists(),
                "Downloaded local research snapshot"
                if Path("data/raw/sider/meddra_all_se.tsv.gz").exists() or Path("data/raw/nsides/OFFSIDES.csv.xz").exists()
                else "Bulk/local research source; run scripts/download_research_sources.py",
            ),
            SourceStatus(
                "Canonical PK/PD dictionary",
                settings.enable_canonical_pkpd,
                settings.enable_canonical_pkpd and Path("data/dictionary/canonical_pkpd.json").exists(),
                "Curated local mechanism seeds",
            ),
        ]
    )

    if settings.llm_provider == "nvidia":
        statuses.append(
            SourceStatus(
                "NVIDIA NIM LLM",
                enabled=True,
                available=bool(settings.nvidia_api_key and settings.nvidia_model),
                reason="NVIDIA_API_KEY and NVIDIA_MODEL configured" if settings.nvidia_api_key and settings.nvidia_model else "Missing NVIDIA_API_KEY or NVIDIA_MODEL",
            )
        )
    elif settings.llm_provider == "ollama":
        statuses.append(SourceStatus("Ollama LLM", enabled=True, available=True, reason=settings.ollama_host))
    else:
        statuses.append(SourceStatus("Mock LLM", enabled=True, available=True, reason="Deterministic local test provider"))

    return statuses
