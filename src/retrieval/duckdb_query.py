# -*- coding: utf-8 -*-
"""
DuckDB retrieval layer over tidy Parquet datasets.

Datasets expected (paths are configurable via base_dir):
  - drugbank.parquet            columns: name_lower, name, synonyms(list), targets(list), ...
  - twosides.parquet            columns: drug_a, drug_b, side_effect, prr
  - offsides.parquet            columns: drug_name, side_effect, prr
  - sider_label_side_effects.parquet columns: drug_name, side_effect, source_label
  - nci_almanac.parquet         columns: drug_a, drug_b, nsc_a, nsc_b, score, panel, cell_name, ...
  - nci_almanac_compounds.parquet columns: nsc, drug_name, display_name
  - dictrank.parquet            columns: drug_name, score
  - dilirank.parquet            columns: drug_name, dili_score
  - diqt.parquet                columns: drug_name, score

Key fixes vs previous version:
  - DIQT no longer uses a hard-coded numeric column; we read tidy (drug_name, score).
  - TwoSides batch branch properly appends side effects (no truncated 'result[d].' line).
  - Exact matching on normalized names (lowercased) instead of prefix LIKE.
  - Views are lightweight; no heavy transforms at runtime.
"""

from __future__ import annotations

import os
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import duckdb
except ImportError:  # DuckDB is an optional evidence backend in demo-safe mode.
    duckdb = None

from src.config.settings import get_settings
from src.core.evidence import EvidenceItem

LOG = logging.getLogger(__name__)


# ---------- Helpers ----------

def _p(*parts: str) -> str:
    return str(Path(*parts))

def _norm_name(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip().replace("\xa0", " ")
    return s.lower()

def _sql_path(path: str) -> str:
    return str(path).replace("'", "''")

def _file_exists(base_dir: str, filename: str) -> bool:
    return Path(base_dir, filename).exists()


_REGISTERED_VIEWS_BY_BASE: Dict[Tuple[str, bool, bool, bool, bool], set[str]] = {}


# ---------- Connection / View Registration ----------

@lru_cache(maxsize=8)
def init_duckdb_connection(
    base_dir: str = "data/duckdb",
    enable_drugbank: Optional[bool] = None,
    enable_duckdb: Optional[bool] = None,
    enable_sider_nsides_offsides: Optional[bool] = None,
    enable_nci_almanac: Optional[bool] = None,
) -> Any:
    """
    Initializes a single DuckDB connection and registers views.
    """
    settings = get_settings()
    base_dir = os.path.abspath(base_dir)
    use_duckdb = settings.enable_duckdb if enable_duckdb is None else bool(enable_duckdb)
    use_drugbank = settings.enable_drugbank if enable_drugbank is None else bool(enable_drugbank)
    use_sider_nsides_offsides = (
        settings.enable_sider_nsides_offsides
        if enable_sider_nsides_offsides is None
        else bool(enable_sider_nsides_offsides)
    )
    use_nci_almanac = settings.enable_nci_almanac if enable_nci_almanac is None else bool(enable_nci_almanac)
    key = (base_dir, use_drugbank, use_duckdb, use_sider_nsides_offsides, use_nci_almanac)
    if duckdb is None:
        _REGISTERED_VIEWS_BY_BASE[key] = set()
        return None
    con = duckdb.connect(database=":memory:")
    if use_duckdb:
        _REGISTERED_VIEWS_BY_BASE[key] = _register_views(
            con,
            base_dir,
            use_drugbank,
            use_sider_nsides_offsides,
            use_nci_almanac,
        )
    else:
        _REGISTERED_VIEWS_BY_BASE[key] = set()
    return con


def _register_views(
    con: Any,
    base_dir: str,
    enable_drugbank: bool = False,
    enable_sider_nsides_offsides: bool = True,
    enable_nci_almanac: bool = False,
) -> set[str]:
    """
    Register simple views over parquet files. We assume the converters already produced tidy schemas.
    """
    registered: set[str] = set()
    drugbank = _p(base_dir, "drugbank.parquet")
    twosides = _p(base_dir, "twosides.parquet")
    offsides = _p(base_dir, "offsides.parquet")
    sider_label_side_effects = _p(base_dir, "sider_label_side_effects.parquet")
    nci_almanac = _p(base_dir, "nci_almanac.parquet")
    nci_almanac_compounds = _p(base_dir, "nci_almanac_compounds.parquet")
    dictrank = _p(base_dir, "dictrank.parquet")
    dilirank = _p(base_dir, "dilirank.parquet")
    diqt = _p(base_dir, "diqt.parquet")

    # DrugBank (lowercase column is already in parquet as name_lower; reassert for safety)
    # Note: enzyme_action_map may not exist in old parquet files, so we handle it gracefully
    # Check if enzyme_action_map column exists in parquet
    if enable_drugbank and _file_exists(base_dir, "drugbank.parquet"):
        try:
            schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(drugbank)}') LIMIT 0").fetchall()
            has_enzyme_action_map = any(col[0] == 'enzyme_action_map' for col in schema)
            if has_enzyme_action_map:
                con.execute(f"""
                    CREATE OR REPLACE VIEW drugbank AS
                    SELECT
                        COALESCE(name_lower, lower(name)) AS name_lower,
                        name,
                        synonyms,
                        targets,
                        target_uniprot,
                        target_actions,
                        enzymes,
                        enzyme_actions,
                        enzyme_action_map,
                        interactions
                    FROM read_parquet('{_sql_path(drugbank)}');
                """)
            else:
                # Backward compatibility: old parquet files without enzyme_action_map
                con.execute(f"""
                    CREATE OR REPLACE VIEW drugbank AS
                    SELECT
                        COALESCE(name_lower, lower(name)) AS name_lower,
                        name,
                        synonyms,
                        targets,
                        target_uniprot,
                        target_actions,
                        enzymes,
                        enzyme_actions,
                        CAST(NULL AS VARCHAR) AS enzyme_action_map,
                        interactions
                    FROM read_parquet('{_sql_path(drugbank)}');
                """)
            registered.add("drugbank")
        except Exception as e:
            LOG.warning("DrugBank parquet was present but could not be registered: %s", e)

    # TwoSides (tidy long)
    if _file_exists(base_dir, "twosides.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW twosides AS
                SELECT
                    CAST(drug_a AS VARCHAR) AS drug_a,
                    CAST(drug_b AS VARCHAR) AS drug_b,
                    CAST(side_effect AS VARCHAR) AS side_effect,
                    CAST(prr AS DOUBLE) AS prr
                FROM read_parquet('{_sql_path(twosides)}');
            """)
            registered.add("twosides")
        except Exception as e:
            LOG.warning("TWOSIDES parquet was present but could not be registered: %s", e)

    # OFFSIDES (single-drug off-label ADE signals)
    if enable_sider_nsides_offsides and _file_exists(base_dir, "offsides.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW offsides AS
                SELECT
                    lower(CAST(drug_name AS VARCHAR)) AS drug_name,
                    CAST(side_effect AS VARCHAR) AS side_effect,
                    CAST(prr AS DOUBLE) AS prr,
                    CAST(mean_reporting_frequency AS DOUBLE) AS mean_reporting_frequency
                FROM read_parquet('{_sql_path(offsides)}');
            """)
            registered.add("offsides")
        except Exception as e:
            LOG.warning("OFFSIDES parquet was present but could not be registered: %s", e)

    # SIDER label-derived side effects
    if enable_sider_nsides_offsides and _file_exists(base_dir, "sider_label_side_effects.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW sider_label_side_effects AS
                SELECT
                    lower(CAST(drug_name AS VARCHAR)) AS drug_name,
                    CAST(stitch_id AS VARCHAR) AS stitch_id,
                    CAST(side_effect AS VARCHAR) AS side_effect,
                    CAST(source_label AS VARCHAR) AS source_label,
                    CAST(meddra_id AS VARCHAR) AS meddra_id
                FROM read_parquet('{_sql_path(sider_label_side_effects)}');
            """)
            registered.add("sider_label_side_effects")
        except Exception as e:
            LOG.warning("SIDER label side-effect parquet was present but could not be registered: %s", e)

    # NCI-ALMANAC experimental oncology combination screen.
    if enable_nci_almanac and _file_exists(base_dir, "nci_almanac.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW nci_almanac AS
                SELECT
                    CAST(combo_drug_seq AS BIGINT) AS combo_drug_seq,
                    CAST(screener AS VARCHAR) AS screener,
                    CAST(study AS VARCHAR) AS study,
                    CAST(test_date AS VARCHAR) AS test_date,
                    CAST(panel_nbr AS BIGINT) AS panel_nbr,
                    CAST(cell_nbr AS BIGINT) AS cell_nbr,
                    lower(CAST(drug_a AS VARCHAR)) AS drug_a,
                    lower(CAST(drug_b AS VARCHAR)) AS drug_b,
                    CAST(drug_a_display AS VARCHAR) AS drug_a_display,
                    CAST(drug_b_display AS VARCHAR) AS drug_b_display,
                    CAST(nsc_a AS VARCHAR) AS nsc_a,
                    CAST(nsc_b AS VARCHAR) AS nsc_b,
                    CAST(sample_a AS VARCHAR) AS sample_a,
                    CAST(sample_b AS VARCHAR) AS sample_b,
                    CAST(conc_index_a AS BIGINT) AS conc_index_a,
                    CAST(conc_index_b AS BIGINT) AS conc_index_b,
                    CAST(conc_a AS DOUBLE) AS conc_a,
                    CAST(conc_b AS DOUBLE) AS conc_b,
                    CAST(conc_unit_a AS VARCHAR) AS conc_unit_a,
                    CAST(conc_unit_b AS VARCHAR) AS conc_unit_b,
                    CAST(percent_growth AS DOUBLE) AS percent_growth,
                    CAST(expected_growth AS DOUBLE) AS expected_growth,
                    CAST(score AS DOUBLE) AS score,
                    CAST(valid AS VARCHAR) AS valid,
                    CAST(panel AS VARCHAR) AS panel,
                    CAST(cell_name AS VARCHAR) AS cell_name
                FROM read_parquet('{_sql_path(nci_almanac)}');
            """)
            registered.add("nci_almanac")
        except Exception as e:
            LOG.warning("NCI-ALMANAC parquet was present but could not be registered: %s", e)

    if enable_nci_almanac and _file_exists(base_dir, "nci_almanac_compounds.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW nci_almanac_compounds AS
                SELECT
                    CAST(nsc AS VARCHAR) AS nsc,
                    lower(CAST(drug_name AS VARCHAR)) AS drug_name,
                    CAST(display_name AS VARCHAR) AS display_name
                FROM read_parquet('{_sql_path(nci_almanac_compounds)}');
            """)
            registered.add("nci_almanac_compounds")
        except Exception as e:
            LOG.warning("NCI-ALMANAC compound alias parquet was present but could not be registered: %s", e)

    # DICTRank
    if _file_exists(base_dir, "dictrank.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW dictrank AS
                SELECT
                    lower(drug_name) AS drug_name,
                    CAST(score AS DOUBLE) AS score
                FROM read_parquet('{_sql_path(dictrank)}');
            """)
            registered.add("dictrank")
        except Exception as e:
            LOG.warning("DICTRank parquet was present but could not be registered: %s", e)

    # DILIRank
    if _file_exists(base_dir, "dilirank.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW dilirank AS
                SELECT
                    lower(drug_name) AS drug_name,
                    CAST(dili_score AS DOUBLE) AS dili_score
                FROM read_parquet('{_sql_path(dilirank)}');
            """)
            registered.add("dilirank")
        except Exception as e:
            LOG.warning("DILIrank parquet was present but could not be registered: %s", e)

    # DIQT (tidy 2-col)
    if _file_exists(base_dir, "diqt.parquet"):
        try:
            con.execute(f"""
                CREATE OR REPLACE VIEW diqt AS
                SELECT
                    lower(drug_name) AS drug_name,
                    CAST(score AS DOUBLE) AS score
                FROM read_parquet('{_sql_path(diqt)}');
            """)
            registered.add("diqt")
        except Exception as e:
            LOG.warning("DIQT parquet was present but could not be registered: %s", e)

    return registered


# ---------- Client API ----------

class DuckDBClient:
    """
    Thin wrapper exposing retrieval functions expected by the RAG pipeline.
    """

    def __init__(
        self,
        base_dir: str = "data/duckdb",
        *,
        enable_drugbank: Optional[bool] = None,
        enable_duckdb: Optional[bool] = None,
        enable_sider_nsides_offsides: Optional[bool] = None,
        enable_nci_almanac: Optional[bool] = None,
    ):
        settings = get_settings()
        self.base_dir = os.path.abspath(base_dir)
        self.enable_duckdb = settings.enable_duckdb if enable_duckdb is None else bool(enable_duckdb)
        self.enable_drugbank = settings.enable_drugbank if enable_drugbank is None else bool(enable_drugbank)
        self.enable_sider_nsides_offsides = (
            settings.enable_sider_nsides_offsides
            if enable_sider_nsides_offsides is None
            else bool(enable_sider_nsides_offsides)
        )
        self.enable_nci_almanac = (
            settings.enable_nci_almanac
            if enable_nci_almanac is None
            else bool(enable_nci_almanac)
        )
        self._con = init_duckdb_connection(
            self.base_dir,
            enable_drugbank=self.enable_drugbank,
            enable_duckdb=self.enable_duckdb,
            enable_sider_nsides_offsides=self.enable_sider_nsides_offsides,
            enable_nci_almanac=self.enable_nci_almanac,
        )
        key = (
            self.base_dir,
            self.enable_drugbank,
            self.enable_duckdb,
            self.enable_sider_nsides_offsides,
            self.enable_nci_almanac,
        )
        self.registered_views = set(_REGISTERED_VIEWS_BY_BASE.get(key, set()))

    def has_view(self, view_name: str) -> bool:
        return view_name in self.registered_views

    def get_available_sources(self) -> Dict[str, bool]:
        return {
            "twosides": self.has_view("twosides"),
            "offsides": self.has_view("offsides"),
            "sider_label_side_effects": self.has_view("sider_label_side_effects"),
            "nci_almanac": self.has_view("nci_almanac") and self.has_view("nci_almanac_compounds"),
            "dilirank": self.has_view("dilirank"),
            "dictrank": self.has_view("dictrank"),
            "diqt": self.has_view("diqt"),
            "drugbank": self.has_view("drugbank"),
        }

    # --- DrugBank ---

    def get_synonyms(self, drug_name: str) -> List[str]:
        d = _norm_name(drug_name)
        if not d or not self.has_view("drugbank"):
            return []
        rows = self._con.execute(
            "SELECT synonyms FROM drugbank WHERE name_lower = ? LIMIT 1;",
            [d],
        ).fetchall()
        if not rows:
            return []
        return [s for s in (rows[0][0] or []) if s]

    # --- Risk Scores ---

    def get_interaction_score(self, drug1: str, drug2: str) -> float:
        """Get PRR (Proportional Reporting Ratio) for a drug pair."""
        a = _norm_name(drug1)
        b = _norm_name(drug2)
        if not a or not b or not self.has_view("twosides"):
            return 0.0
        rows = self._con.execute(
            """
            SELECT MAX(prr) AS prr
            FROM twosides
            WHERE ((drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?))
            LIMIT 1;
            """,
            [a, b, b, a],
        ).fetchall()
        return float(rows[0][0]) if rows and rows[0][0] is not None else 0.0

    def _get_nci_nscs(self, drug_name: str, *, limit: int = 50) -> List[str]:
        d = _norm_name(drug_name)
        if not d or not self.has_view("nci_almanac_compounds"):
            return []

        rows = self._con.execute(
            """
            SELECT DISTINCT nsc
            FROM nci_almanac_compounds
            WHERE drug_name = ?
            ORDER BY nsc
            LIMIT ?;
            """,
            [d, int(limit)],
        ).fetchall()
        if not rows:
            rows = self._con.execute(
                """
                SELECT DISTINCT nsc
                FROM nci_almanac_compounds
                WHERE drug_name LIKE ?
                ORDER BY length(drug_name), drug_name, nsc
                LIMIT ?;
                """,
                [f"%{d}%", int(limit)],
            ).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]

    def get_nci_almanac_pair(self, drug_a: str, drug_b: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """Return strongest NCI-ALMANAC experimental combo-screen rows for a drug pair.

        This is oncology cell-line screening evidence. It is useful for hypothesis
        generation and mechanism review, but it is not clinical DDI incidence,
        dose-adjustment guidance, or patient safety outcome evidence.
        """
        a = _norm_name(drug_a)
        b = _norm_name(drug_b)
        if not a or not b or not self.has_view("nci_almanac") or not self.has_view("nci_almanac_compounds"):
            return []

        nsc_a = self._get_nci_nscs(a)
        nsc_b = self._get_nci_nscs(b)
        if not nsc_a or not nsc_b:
            return []

        placeholders_a = ", ".join(["?"] * len(nsc_a))
        placeholders_b = ", ".join(["?"] * len(nsc_b))
        params: List[Any] = nsc_a + nsc_b + nsc_b + nsc_a + [int(top_k)]
        rows = self._con.execute(
            f"""
            SELECT
                drug_a_display,
                drug_b_display,
                nsc_a,
                nsc_b,
                sample_a,
                sample_b,
                panel,
                cell_name,
                conc_a,
                conc_unit_a,
                conc_b,
                conc_unit_b,
                percent_growth,
                expected_growth,
                score,
                valid,
                study
            FROM nci_almanac
            WHERE (
                    (nsc_a IN ({placeholders_a}) AND nsc_b IN ({placeholders_b}))
                 OR (nsc_a IN ({placeholders_b}) AND nsc_b IN ({placeholders_a}))
            )
              AND (upper(COALESCE(valid, 'Y')) = 'Y')
            ORDER BY COALESCE(score, 0) DESC, panel, cell_name
            LIMIT ?;
            """,
            params,
        ).fetchall()

        keys = [
            "drug_a",
            "drug_b",
            "nsc_a",
            "nsc_b",
            "sample_a",
            "sample_b",
            "panel",
            "cell_name",
            "conc_a",
            "conc_unit_a",
            "conc_b",
            "conc_unit_b",
            "percent_growth",
            "expected_growth",
            "score",
            "valid",
            "study",
        ]
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(zip(keys, row))
            item["score_definition"] = "expected_growth - percent_growth"
            item["score_direction"] = "higher positive score means stronger-than-expected cell growth inhibition"
            item["evidence_scope"] = "experimental oncology cell-line combination screen; hypothesis support only"
            out.append(item)
        return out

    def get_dict_rank(self, drug_name: str | List[str]) -> str | Dict[str, str]:
        """Get DICT rank (severity) for a drug or list of drugs."""
        if isinstance(drug_name, list):
            return self._get_dict_rank_batch(drug_name)
        d = _norm_name(drug_name)
        if not d or not self.has_view("dictrank"):
            return "unknown"
        # Try exact match first
        rows = self._con.execute(
            "SELECT score FROM dictrank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        if not rows or rows[0][0] is None:
            # Try partial match
            rows = self._con.execute(
                "SELECT score FROM dictrank WHERE drug_name LIKE ? LIMIT 1;",
                [f"%{d}%"],
            ).fetchall()
        if not rows or rows[0][0] is None:
            return "unknown"
        # Convert score to severity string
        try:
            score = float(rows[0][0])
        except (ValueError, TypeError):
            return "unknown"
        if score >= 0.7:
            return "severe"
        elif score >= 0.4:
            return "moderate"
        elif score >= 0.1:
            return "mild"
        else:
            return "low"

    def get_dili_risk(self, drug_name: str | List[str]) -> str | None | Dict[str, str]:
        """Get DILI risk for a drug or list of drugs."""
        if isinstance(drug_name, list):
            return self._get_dili_risk_batch(drug_name)
        d = _norm_name(drug_name)
        if not d or not self.has_view("dilirank"):
            return None
        # Try exact match first
        rows = self._con.execute(
            "SELECT dili_score FROM dilirank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        if not rows or rows[0][0] is None:
            # Try partial match
            rows = self._con.execute(
                "SELECT dili_score FROM dilirank WHERE drug_name LIKE ? LIMIT 1;",
                [f"%{d}%"],
            ).fetchall()
        if not rows or rows[0][0] is None:
            return None
        # Convert score to risk category
        try:
            score = float(rows[0][0])
        except (ValueError, TypeError):
            return None
        if score >= 0.7:
            return "high"
        elif score >= 0.4:
            return "medium"
        else:
            return "low"

    def get_diqt_score(self, drug_name: str | List[str]) -> Optional[float] | Dict[str, Optional[float]]:
        """Get DIQT score for a drug or list of drugs."""
        if isinstance(drug_name, list):
            return self._get_diqt_score_batch(drug_name)
        d = _norm_name(drug_name)
        if not d or not self.has_view("diqt"):
            return None
        # Try exact match first
        rows = self._con.execute(
            "SELECT score FROM diqt WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                pass
        # Try partial match (e.g., "warfarin" matches "warfarin sodium")
        rows = self._con.execute(
            "SELECT score FROM diqt WHERE drug_name LIKE ? LIMIT 1;",
            [f"%{d}%"],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                return None
        return None

    # Legacy aliases for backward compatibility
    def get_dictrank_score(self, drug_name: str) -> Optional[float]:
        d = _norm_name(drug_name)
        if not d or not self.has_view("dictrank"):
            return None
        # Try exact match first
        rows = self._con.execute(
            "SELECT score FROM dictrank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                return None
        # Try partial match (e.g., "warfarin" matches "warfarin sodium")
        rows = self._con.execute(
            "SELECT score FROM dictrank WHERE drug_name LIKE ? LIMIT 1;",
            [f"%{d}%"],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                return None
        return None

    def get_dilirank_score(self, drug_name: str) -> Optional[float]:
        d = _norm_name(drug_name)
        if not d or not self.has_view("dilirank"):
            return None
        # Try exact match first
        rows = self._con.execute(
            "SELECT dili_score FROM dilirank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                return None
        # Try partial match (e.g., "warfarin" matches "warfarin sodium")
        rows = self._con.execute(
            "SELECT dili_score FROM dilirank WHERE drug_name LIKE ? LIMIT 1;",
            [f"%{d}%"],
        ).fetchall()
        if rows and rows[0][0] is not None:
            try:
                return float(rows[0][0])
            except (ValueError, TypeError):
                return None
        return None

    # --- TwoSides side-effects ---

    def get_side_effects(
        self,
        drug_a: str | List[str],
        drug_b: Optional[str] = None,
        top_k: int = 20,
        min_prr: float = 1.0,
    ) -> List[str] | Dict[str, List[str]]:
        """
        For single drug: return list of side effects.
        For list of drugs: return dict mapping drug to list of side effects.
        For pair (A,B): return side effects observed with the pair (order-insensitive).
        """
        # Batch mode: list of drugs
        if isinstance(drug_a, list):
            return self.get_side_effects_batch(drug_a, top_k_per_drug=top_k, min_prr=min_prr)

        a = _norm_name(drug_a)
        b = _norm_name(drug_b) if drug_b else None

        if not a:
            return []

        if b is None:
            parts: List[str] = []
            params: List[Any] = []
            if self.has_view("twosides"):
                parts.append(
                    """
                    SELECT side_effect, MAX(prr) AS score, 1 AS source_priority
                    FROM twosides
                    WHERE (drug_a = ? OR drug_b = ?)
                      AND (prr IS NULL OR prr >= ?)
                    GROUP BY side_effect
                    """
                )
                params.extend([a, a, float(min_prr)])
            if self.has_view("offsides"):
                parts.append(
                    """
                    SELECT side_effect, MAX(prr) AS score, 2 AS source_priority
                    FROM offsides
                    WHERE drug_name = ?
                      AND (prr IS NULL OR prr >= ?)
                    GROUP BY side_effect
                    """
                )
                params.extend([a, float(min_prr)])
            if self.has_view("sider_label_side_effects"):
                parts.append(
                    """
                    SELECT side_effect, CAST(NULL AS DOUBLE) AS score, 3 AS source_priority
                    FROM sider_label_side_effects
                    WHERE drug_name = ?
                    GROUP BY side_effect
                    """
                )
                params.append(a)
            if not parts:
                return []
            sql = f"""
                WITH all_effects AS (
                    {' UNION ALL '.join(parts)}
                )
                SELECT side_effect, MAX(score) AS score, MIN(source_priority) AS source_priority
                FROM all_effects
                GROUP BY side_effect
                ORDER BY COALESCE(MAX(score), 0) DESC, MIN(source_priority), side_effect
                LIMIT ?;
            """
            rows = self._con.execute(sql, params + [int(top_k)]).fetchall()
            return [r[0] for r in rows]

        # Pair (order-insensitive)
        if not self.has_view("twosides"):
            return []
        rows = self._con.execute(
            """
            SELECT side_effect, MAX(prr) AS prr
            FROM twosides
            WHERE ((drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?))
              AND (prr IS NULL OR prr >= ?)
            GROUP BY side_effect
            ORDER BY COALESCE(MAX(prr), 0) DESC, side_effect
            LIMIT ?;
            """,
            [a, b, b, a, float(min_prr), int(top_k)],
        ).fetchall()
        return [r[0] for r in rows]

    # --- Batch helpers ---

    def get_side_effects_batch(
        self, drugs: Iterable[str], top_k_per_drug: int = 10, min_prr: float = 1.0
    ) -> Dict[str, List[str]]:
        """
        Batch version for single-drug side effects (used for building context quickly).
        Returns {drug: [side_effects...]}.
        """
        normed = [d for d in {_norm_name(x) for x in drugs} if d]
        if not normed:
            return {}
        if not self.has_view("twosides"):
            return {d: [] for d in normed}

        # Query all at once via IN (...) for both A and B
        placeholders = ", ".join(["?"] * len(normed))
        params = normed + normed + [float(min_prr), int(top_k_per_drug)]

        rows = self._con.execute(
            f"""
            WITH all_rows AS (
              SELECT drug_a AS drug, side_effect, prr FROM twosides WHERE drug_a IN ({placeholders})
              UNION ALL
              SELECT drug_b AS drug, side_effect, prr FROM twosides WHERE drug_b IN ({placeholders})
            )
            SELECT drug, side_effect, MAX(prr) AS prr
            FROM all_rows
            WHERE (prr IS NULL OR prr >= ?)
            GROUP BY drug, side_effect
            QUALIFY ROW_NUMBER() OVER (PARTITION BY drug ORDER BY COALESCE(MAX(prr),0) DESC, side_effect) <= ?
            ORDER BY drug;
            """,
            params,
        ).fetchall()

        result: Dict[str, List[str]] = {d: [] for d in normed}
        for drug, se, _prr in rows:
            if drug not in result:
                result[drug] = []
            result[drug].append(se)

        # dedupe per drug, keep order
        for d in list(result.keys()):
            seen = set()
            uniq: List[str] = []
            for se in result[d]:
                if se not in seen:
                    uniq.append(se)
                    seen.add(se)
            result[d] = uniq
        return result

    def _get_dict_rank_batch(self, drugs: List[str]) -> Dict[str, str]:
        """Batch version of get_dict_rank."""
        normed = [_norm_name(d) for d in drugs if _norm_name(d)]
        if not normed:
            return {}
        if not self.has_view("dictrank"):
            return {d: "unknown" for d in drugs}
        placeholders = ", ".join(["?"] * len(normed))
        rows = self._con.execute(
            f"""
            SELECT drug_name, score
            FROM dictrank
            WHERE drug_name IN ({placeholders});
            """,
            normed,
        ).fetchall()

        result: Dict[str, str] = {}
        for d in drugs:
            d_norm = _norm_name(d)
            result[d] = "unknown"
            for row in rows:
                if row[0] == d_norm:
                    score = float(row[1]) if row[1] is not None else 0.0
                    if score >= 0.7:
                        result[d] = "severe"
                    elif score >= 0.4:
                        result[d] = "moderate"
                    elif score >= 0.1:
                        result[d] = "mild"
                    else:
                        result[d] = "low"
                    break
        return result

    def _get_dili_risk_batch(self, drugs: List[str]) -> Dict[str, str]:
        """Batch version of get_dili_risk."""
        normed = [_norm_name(d) for d in drugs if _norm_name(d)]
        if not normed:
            return {}
        if not self.has_view("dilirank"):
            return {d: "unknown" for d in drugs}
        placeholders = ", ".join(["?"] * len(normed))
        rows = self._con.execute(
            f"""
            SELECT drug_name, dili_score
            FROM dilirank
            WHERE drug_name IN ({placeholders});
            """,
            normed,
        ).fetchall()

        result: Dict[str, str] = {}
        for d in drugs:
            d_norm = _norm_name(d)
            result[d] = "unknown"
            for row in rows:
                if row[0] == d_norm:
                    score = float(row[1]) if row[1] is not None else 0.0
                    if score >= 0.7:
                        result[d] = "high"
                    elif score >= 0.4:
                        result[d] = "medium"
                    else:
                        result[d] = "low"
                    break
        return result

    def _get_diqt_score_batch(self, drugs: List[str]) -> Dict[str, Optional[float]]:
        """Batch version of get_diqt_score."""
        normed = [_norm_name(d) for d in drugs if _norm_name(d)]
        if not normed:
            return {}
        if not self.has_view("diqt"):
            return {d: None for d in drugs}
        placeholders = ", ".join(["?"] * len(normed))
        rows = self._con.execute(
            f"""
            SELECT drug_name, score
            FROM diqt
            WHERE drug_name IN ({placeholders});
            """,
            normed,
        ).fetchall()

        result: Dict[str, Optional[float]] = {d: None for d in drugs}
        for d in drugs:
            d_norm = _norm_name(d)
            for row in rows:
                if row[0] == d_norm:
                    result[d] = float(row[1]) if row[1] is not None else None
                    break
        return result

    def get_drug_enzymes(self, drug_name: str) -> Dict[str, Any]:
        """
        Return enzyme data from DrugBank: enzymes and their actions (substrate/inhibitor/inducer).
        Returns dict with:
        - 'enzymes' (list of enzyme names)
        - 'enzyme_actions' (list of all actions, flattened - for backward compatibility)
        - 'enzyme_action_map' (list of dicts with 'enzyme' and 'actions' keys - proper mapping)
        """
        d = _norm_name(drug_name)
        if not d or not self.has_view("drugbank"):
            return {"enzymes": [], "enzyme_actions": [], "enzyme_action_map": []}

        # Try exact match first
        rows = self._con.execute(
            "SELECT enzymes, enzyme_actions, enzyme_action_map FROM drugbank WHERE name_lower = ? LIMIT 1;",
            [d],
        ).fetchall()
        if rows and rows[0][0]:
            enzymes = rows[0][0] or []
            enzyme_actions = rows[0][1] or []
            enzyme_action_map_str = rows[0][2] if len(rows[0]) > 2 else None

            # Parse enzyme_action_map if available
            enzyme_action_map = []
            if enzyme_action_map_str:
                try:
                    import json
                    enzyme_action_map = json.loads(enzyme_action_map_str)
                except Exception:
                    pass

            return {
                "enzymes": enzymes,
                "enzyme_actions": enzyme_actions,
                "enzyme_action_map": enzyme_action_map
            }

        # Try partial match
        rows = self._con.execute(
            "SELECT enzymes, enzyme_actions, enzyme_action_map FROM drugbank WHERE name_lower LIKE ? LIMIT 1;",
            [f"%{d}%"],
        ).fetchall()
        if rows and rows[0][0]:
            enzymes = rows[0][0] or []
            enzyme_actions = rows[0][1] or []
            enzyme_action_map_str = rows[0][2] if len(rows[0]) > 2 else None

            enzyme_action_map = []
            if enzyme_action_map_str:
                try:
                    import json
                    enzyme_action_map = json.loads(enzyme_action_map_str)
                except Exception:
                    pass

            return {
                "enzymes": enzymes,
                "enzyme_actions": enzyme_actions,
                "enzyme_action_map": enzyme_action_map
            }

        return {"enzymes": [], "enzyme_actions": [], "enzyme_action_map": []}

    def get_drug_targets(self, drug_name: str | List[str]) -> List[str] | Dict[str, List[str]]:
        """Get drug targets for a single drug or batch of drugs."""
        if isinstance(drug_name, list):
            return self._get_drug_targets_batch(drug_name)
        d = _norm_name(drug_name)
        if not d or not self.has_view("drugbank"):
            return []
        rows = self._con.execute(
            "SELECT targets FROM drugbank WHERE name_lower = ? LIMIT 1;",
            [d],
        ).fetchall()
        if not rows:
            return []
        return [t for t in (rows[0][0] or []) if t]

    def _get_drug_targets_batch(self, drugs: List[str]) -> Dict[str, List[str]]:
        """Batch version of get_drug_targets."""
        normed = [_norm_name(d) for d in drugs if _norm_name(d)]
        if not normed:
            return {}
        if not self.has_view("drugbank"):
            return {d: [] for d in drugs}
        placeholders = ", ".join(["?"] * len(normed))
        rows = self._con.execute(
            f"""
            SELECT name_lower, targets
            FROM drugbank
            WHERE name_lower IN ({placeholders});
            """,
            normed,
        ).fetchall()

        result: Dict[str, List[str]] = {d: [] for d in drugs}
        for d in drugs:
            d_norm = _norm_name(d)
            for row in rows:
                if row[0] == d_norm:
                    result[d] = [t for t in (row[1] or []) if t]
                    break
        return result

    def get_pair_evidence(self, drug_a: str, drug_b: str, top_k: int = 20) -> List[EvidenceItem]:
        a = _norm_name(drug_a)
        b = _norm_name(drug_b)
        if not a or not b or not self.has_view("twosides"):
            return []

        rows = self._con.execute(
            """
            SELECT side_effect, MAX(prr) AS prr
            FROM twosides
            WHERE ((drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?))
            GROUP BY side_effect
            ORDER BY COALESCE(MAX(prr), 0) DESC, side_effect
            LIMIT ?;
            """,
            [a, b, b, a, int(top_k)],
        ).fetchall()

        return [
            EvidenceItem(
                source="TWOSIDES",
                evidence_type="pair_adverse_event_signal",
                subject=f"{a} + {b}",
                predicate="reported_pair_side_effect",
                object=str(side_effect),
                confidence="associative",
                raw={"prr": prr},
            )
            for side_effect, prr in rows
            if side_effect
        ]

    def get_drug_toxicity_evidence(self, drug_name: str) -> List[EvidenceItem]:
        d = _norm_name(drug_name)
        if not d:
            return []

        items: List[EvidenceItem] = []
        dili = self.get_dilirank_score(d)
        if dili is not None:
            items.append(
                EvidenceItem(
                    source="DILIrank",
                    evidence_type="single_drug_toxicity",
                    subject=d,
                    predicate="dili_score",
                    object=dili,
                    confidence="dataset_score",
                )
            )

        dictrank = self.get_dictrank_score(d)
        if dictrank is not None:
            items.append(
                EvidenceItem(
                    source="DICTRank",
                    evidence_type="single_drug_toxicity",
                    subject=d,
                    predicate="cardiotoxicity_score",
                    object=dictrank,
                    confidence="dataset_score",
                )
            )

        diqt = self.get_diqt_score(d)
        if diqt is not None:
            items.append(
                EvidenceItem(
                    source="DIQT",
                    evidence_type="single_drug_toxicity",
                    subject=d,
                    predicate="qt_risk_score",
                    object=diqt,
                    confidence="dataset_score",
                )
            )

        return items
