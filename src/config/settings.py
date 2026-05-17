from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

DataMode = Literal["public_safe", "local_dev", "full_research_future"]

_VALID_DATA_MODES = {"public_safe", "local_dev", "full_research_future"}


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


@dataclass(frozen=True)
class Settings:
    data_mode: DataMode = "public_safe"
    data_manifest_path: str = "data_manifest.yaml"
    duckdb_dir: str = "data/duckdb"
    openfda_cache_dir: str = "data/cache/openfda"

    enable_duckdb: bool = True
    enable_drugbank: bool = False
    enable_qlever: bool = False
    enable_pubchem_rest: bool = True
    enable_pubchem_pugview: bool = True
    enable_openfda: bool = True
    enable_chembl: bool = True
    enable_kegg: bool = True
    enable_reactome: bool = True
    enable_uniprot: bool = True
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
    llm_temperature: float = 1.0
    llm_top_p: float = 1.0
    llm_max_tokens: int = 4096
    llm_stream: bool = False
    llm_timeout_s: float = 300.0


def get_settings() -> Settings:
    _load_dotenv_once()

    mode = _env_str("INFERMED_DATA_MODE", "public_safe").lower()
    if mode not in _VALID_DATA_MODES:
        mode = "public_safe"

    enable_drugbank = _env_bool("ENABLE_DRUGBANK", False)
    enable_qlever = _env_bool("ENABLE_QLEVER", False)

    # Public-safe mode must not touch restricted DrugBank data or QLever runtime.
    if mode == "public_safe":
        enable_drugbank = False
        enable_qlever = False

    return Settings(
        data_mode=mode,  # type: ignore[arg-type]
        data_manifest_path=_env_str("DATA_MANIFEST_PATH", "data_manifest.yaml"),
        duckdb_dir=_env_str("DUCKDB_DIR", "data/duckdb"),
        openfda_cache_dir=_env_str("OPENFDA_CACHE_DIR", "data/cache/openfda"),
        enable_duckdb=_env_bool("ENABLE_DUCKDB", True),
        enable_drugbank=enable_drugbank,
        enable_qlever=enable_qlever,
        enable_pubchem_rest=True,
        enable_pubchem_pugview=True,
        enable_openfda=_env_bool("ENABLE_OPENFDA", True),
        enable_chembl=True,
        enable_kegg=True,
        enable_reactome=True,
        enable_uniprot=True,
        enable_canonical_pkpd=_env_bool("ENABLE_CANONICAL_PKPD", True),
        enable_semantic_search=_env_bool("ENABLE_SEMANTIC_SEARCH", False),
        enable_reranking=_env_bool("ENABLE_RERANKING", False),
        llm_provider=_env_str("LLM_PROVIDER", "mock").lower(),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
        nvidia_base_url=_env_str("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        nvidia_model=_env_str("NVIDIA_MODEL", ""),
        nvidia_reasoning_effort=_env_str("NVIDIA_REASONING_EFFORT", "").lower(),
        ollama_host=_env_str("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=_env_str("OLLAMA_MODEL", "gpt-oss"),
        llm_temperature=_env_float("LLM_TEMPERATURE", _env_float("NVIDIA_TEMPERATURE", 1.0)),
        llm_top_p=_env_float("LLM_TOP_P", _env_float("NVIDIA_TOP_P", 1.0)),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", _env_int("NVIDIA_MAX_TOKENS", 4096)),
        llm_stream=_env_bool("LLM_STREAM", _env_bool("NVIDIA_STREAM", False)),
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", _env_float("NVIDIA_TIMEOUT_S", 300.0)),
    )
