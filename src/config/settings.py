from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

DataMode = Literal["public_safe", "local_dev", "full_research_future"]

_VALID_DATA_MODES = {"public_safe", "local_dev", "full_research_future"}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA_CONFIG_PATH = "data_manifest.yaml"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _nested_get(payload: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


@lru_cache(maxsize=1)
def _load_dotenv_once() -> None:
    if _env_bool("INFERMED_SKIP_DOTENV", False):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


@lru_cache(maxsize=1)
def _load_data_config() -> dict[str, Any]:
    """Load non-secret data/runtime configuration from YAML.

    The project .env should contain provider credentials and model/runtime
    secrets only. Data mode, source toggles, local paths, and cache policy live
    in this YAML file.
    """

    raw_path = os.getenv("INFERMED_DATA_CONFIG", _DEFAULT_DATA_CONFIG_PATH).strip() or _DEFAULT_DATA_CONFIG_PATH
    path = Path(raw_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _data_env_overrides_enabled() -> bool:
    """Compatibility escape hatch for tests/CI.

    Runtime `.env` files should not set data controls anymore. Tests can enable
    this explicitly when they need to monkeypatch historical ENABLE_* flags.
    """

    return _env_bool("INFERMED_ALLOW_DATA_ENV_OVERRIDES", False)


def _config_str(config: dict[str, Any], path: str, default: str, *, env_name: str | None = None) -> str:
    if env_name and _data_env_overrides_enabled():
        raw = os.getenv(env_name)
        if raw is not None:
            return raw.strip()
    return _as_str(_nested_get(config, path, default), default)


def _config_bool(config: dict[str, Any], path: str, default: bool, *, env_name: str | None = None) -> bool:
    if env_name and _data_env_overrides_enabled() and os.getenv(env_name) is not None:
        return _env_bool(env_name, default)
    return _as_bool(_nested_get(config, path, default), default)


def _config_int(config: dict[str, Any], path: str, default: int, *, env_name: str | None = None) -> int:
    if env_name and _data_env_overrides_enabled() and os.getenv(env_name) is not None:
        return _env_int(env_name, default)
    return _as_int(_nested_get(config, path, default), default)


@dataclass(frozen=True)
class Settings:
    data_mode: DataMode = "public_safe"
    data_manifest_path: str = "data_manifest.yaml"
    duckdb_dir: str = "data/duckdb"
    openfda_cache_dir: str = "data/cache/openfda"
    cache_backend: str = "file"
    sqlite_cache_path: str = "data/cache/infermed_cache.sqlite"
    enable_event_store: bool = False
    openfda_ttl_days: int = 30
    chembl_timeout_s: int = 10

    enable_duckdb: bool = True
    enable_drugbank: bool = False
    enable_qlever: bool = False
    enable_pubchem_rest: bool = True
    enable_pubchem_pugview: bool = True
    enable_openfda: bool = True
    enable_openfda_label: bool = True
    enable_dailymed: bool = True
    enable_rxnorm: bool = True
    enable_chembl: bool = True
    enable_kegg: bool = True
    enable_reactome: bool = True
    enable_uniprot: bool = True
    enable_fda_pgx: bool = True
    enable_europe_pmc: bool = True
    enable_open_targets: bool = True
    enable_stringdb: bool = True
    enable_biogrid: bool = False
    enable_drugcentral: bool = True
    enable_nci_almanac: bool = False
    enable_sider_nsides_offsides: bool = True
    enable_canonical_pkpd: bool = True
    enable_semantic_search: bool = False
    enable_reranking: bool = False

    llm_provider: str = "mock"
    nvidia_api_key: str = field(default="", repr=False)
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = ""
    nvidia_reasoning_effort: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gpt-oss"
    ollama_timeout_s: float = 5000.0
    ollama_num_predict: int = -1
    string_caller_identity: str = "infermed-research"
    biogrid_access_key: str = field(default="", repr=False)
    llm_temperature: float = 1.0
    llm_top_p: float = 1.0
    llm_max_tokens: int = 4096
    llm_stream: bool = False
    llm_timeout_s: float = 300.0


def get_settings() -> Settings:
    _load_dotenv_once()
    config = _load_data_config()

    mode = _config_str(config, "runtime.data_mode", "public_safe", env_name="INFERMED_DATA_MODE").lower()
    if mode not in _VALID_DATA_MODES:
        mode = "public_safe"

    enable_drugbank = _config_bool(config, "runtime.sources.drugbank", False, env_name="ENABLE_DRUGBANK")
    enable_qlever = _config_bool(config, "runtime.sources.qlever", False, env_name="ENABLE_QLEVER")

    # Public-safe mode must not touch restricted DrugBank data or QLever runtime.
    if mode == "public_safe":
        enable_drugbank = False
        enable_qlever = False

    cache_backend = _config_str(config, "runtime.cache.backend", "file", env_name="CACHE_BACKEND").lower()

    return Settings(
        data_mode=mode,  # type: ignore[arg-type]
        data_manifest_path=_config_str(config, "runtime.data_manifest_path", _DEFAULT_DATA_CONFIG_PATH, env_name="DATA_MANIFEST_PATH"),
        duckdb_dir=_config_str(config, "runtime.paths.duckdb_dir", "data/duckdb", env_name="DUCKDB_DIR"),
        openfda_cache_dir=_config_str(config, "runtime.paths.openfda_cache_dir", "data/cache/openfda", env_name="OPENFDA_CACHE_DIR"),
        cache_backend=cache_backend,
        sqlite_cache_path=_config_str(config, "runtime.cache.sqlite_cache_path", "data/cache/infermed_cache.sqlite", env_name="SQLITE_CACHE_PATH"),
        enable_event_store=_config_bool(config, "runtime.cache.enable_event_store", cache_backend == "sqlite", env_name="ENABLE_EVENT_STORE"),
        openfda_ttl_days=_config_int(config, "runtime.source_settings.openfda_ttl_days", 30, env_name="OPENFDA_TTL_DAYS"),
        chembl_timeout_s=_config_int(config, "runtime.source_settings.chembl_timeout_s", 10, env_name="CHEMBL_TIMEOUT"),
        enable_duckdb=_config_bool(config, "runtime.sources.duckdb", True, env_name="ENABLE_DUCKDB"),
        enable_drugbank=enable_drugbank,
        enable_qlever=enable_qlever,
        enable_pubchem_rest=_config_bool(config, "runtime.sources.pubchem_rest", True),
        enable_pubchem_pugview=_config_bool(config, "runtime.sources.pubchem_pugview", True),
        enable_openfda=_config_bool(config, "runtime.sources.openfda", True, env_name="ENABLE_OPENFDA"),
        enable_openfda_label=_config_bool(config, "runtime.sources.openfda_label", True, env_name="ENABLE_OPENFDA_LABEL"),
        enable_dailymed=_config_bool(config, "runtime.sources.dailymed", True, env_name="ENABLE_DAILYMED"),
        enable_rxnorm=_config_bool(config, "runtime.sources.rxnorm", True, env_name="ENABLE_RXNORM"),
        enable_chembl=_config_bool(config, "runtime.sources.chembl", True),
        enable_kegg=_config_bool(config, "runtime.sources.kegg", True),
        enable_reactome=_config_bool(config, "runtime.sources.reactome", True),
        enable_uniprot=_config_bool(config, "runtime.sources.uniprot", True),
        enable_fda_pgx=_config_bool(config, "runtime.sources.fda_pgx", True, env_name="ENABLE_FDA_PGX"),
        enable_europe_pmc=_config_bool(config, "runtime.sources.europe_pmc", True, env_name="ENABLE_EUROPE_PMC"),
        enable_open_targets=_config_bool(config, "runtime.sources.open_targets", True, env_name="ENABLE_OPEN_TARGETS"),
        enable_stringdb=_config_bool(config, "runtime.sources.stringdb", True, env_name="ENABLE_STRINGDB"),
        enable_biogrid=_config_bool(config, "runtime.sources.biogrid", bool(_env_str("BIOGRID_ACCESS_KEY", "")), env_name="ENABLE_BIOGRID"),
        enable_drugcentral=_config_bool(config, "runtime.sources.drugcentral", True, env_name="ENABLE_DRUGCENTRAL"),
        enable_nci_almanac=_config_bool(config, "runtime.sources.nci_almanac", False, env_name="ENABLE_NCI_ALMANAC"),
        enable_sider_nsides_offsides=_config_bool(config, "runtime.sources.sider_nsides_offsides", True, env_name="ENABLE_SIDER_NSIDES_OFFSIDES"),
        enable_canonical_pkpd=_config_bool(config, "runtime.sources.canonical_pkpd", True, env_name="ENABLE_CANONICAL_PKPD"),
        enable_semantic_search=_config_bool(config, "runtime.sources.semantic_search", False, env_name="ENABLE_SEMANTIC_SEARCH"),
        enable_reranking=_config_bool(config, "runtime.sources.reranking", False, env_name="ENABLE_RERANKING"),
        llm_provider=_env_str("LLM_PROVIDER", "mock").lower(),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
        nvidia_base_url=_env_str("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        nvidia_model=_env_str("NVIDIA_MODEL", ""),
        nvidia_reasoning_effort=_env_str("NVIDIA_REASONING_EFFORT", "").lower(),
        ollama_host=_env_str("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=_env_str("OLLAMA_MODEL", "gpt-oss"),
        ollama_timeout_s=_env_float("OLLAMA_TIMEOUT_S", _env_float("LLM_TIMEOUT_S", 5000.0)),
        ollama_num_predict=_env_int("OLLAMA_NUM_PREDICT", -1),
        string_caller_identity=_env_str("STRING_CALLER_IDENTITY", "infermed-research"),
        biogrid_access_key=os.getenv("BIOGRID_ACCESS_KEY", ""),
        llm_temperature=_env_float("LLM_TEMPERATURE", _env_float("NVIDIA_TEMPERATURE", 1.0)),
        llm_top_p=_env_float("LLM_TOP_P", _env_float("NVIDIA_TOP_P", 1.0)),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", _env_int("NVIDIA_MAX_TOKENS", 4096)),
        llm_stream=_env_bool("LLM_STREAM", _env_bool("NVIDIA_STREAM", False)),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", _env_float("NVIDIA_TIMEOUT_S", 300.0)),
    )
