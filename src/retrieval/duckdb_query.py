# -*- coding: utf-8 -*-
"""
DuckDB retrieval layer over tidy Parquet datasets.

Datasets expected (paths are configurable via base_dir):
  - drugbank.parquet            columns: name_lower, name, synonyms(list), targets(list), ...
  - twosides.parquet            columns: drug_a, drug_b, side_effect, prr
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
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import duckdb


# ---------- Helpers ----------

def _p(*parts: str) -> str:
    return str(Path(*parts))

def _norm_name(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip().replace("\xa0", " ")
    return s.lower()


# ---------- Connection / View Registration ----------

@lru_cache(maxsize=1)
def init_duckdb_connection(base_dir: str = "data/duckdb") -> duckdb.DuckDBPyConnection:
    """
    Initializes a single DuckDB connection and registers views.
    """
    base_dir = os.path.abspath(base_dir)
    con = duckdb.connect(database=":memory:")
    _register_views(con, base_dir)
    return con


def _register_views(con: duckdb.DuckDBPyConnection, base_dir: str) -> None:
    """
    Register simple views over parquet files. We assume the converters already produced tidy schemas.
    """
    drugbank = _p(base_dir, "drugbank.parquet")
    twosides = _p(base_dir, "twosides.parquet")
    dictrank = _p(base_dir, "dictrank.parquet")
    dilirank = _p(base_dir, "dilirank.parquet")
    diqt = _p(base_dir, "diqt.parquet")

    # DrugBank (lowercase column is already in parquet as name_lower; reassert for safety)
    # Note: enzyme_action_map may not exist in old parquet files, so we handle it gracefully
    # Check if enzyme_action_map column exists in parquet
    try:
        schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{drugbank}') LIMIT 0").fetchall()
        has_enzyme_action_map = any(col[0] == 'enzyme_action_map' for col in schema)
    except Exception:
        has_enzyme_action_map = False
    
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
            FROM read_parquet('{drugbank}');
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
            FROM read_parquet('{drugbank}');
        """)

    # TwoSides (tidy long)
    con.execute(f"""
        CREATE OR REPLACE VIEW twosides AS
        SELECT 
            CAST(drug_a AS VARCHAR) AS drug_a,
            CAST(drug_b AS VARCHAR) AS drug_b,
            CAST(side_effect AS VARCHAR) AS side_effect,
            CAST(prr AS DOUBLE) AS prr
        FROM read_parquet('{twosides}');
    """)

    # DICTRank
    con.execute(f"""
        CREATE OR REPLACE VIEW dictrank AS
        SELECT 
            lower(drug_name) AS drug_name,
            CAST(score AS DOUBLE) AS score
        FROM read_parquet('{dictrank}');
    """)

    # DILIRank
    con.execute(f"""
        CREATE OR REPLACE VIEW dilirank AS
        SELECT 
            lower(drug_name) AS drug_name,
            CAST(dili_score AS DOUBLE) AS dili_score
        FROM read_parquet('{dilirank}');
    """)

    # DIQT (tidy 2-col)
    con.execute(f"""
        CREATE OR REPLACE VIEW diqt AS
        SELECT 
            lower(drug_name) AS drug_name,
            CAST(score AS DOUBLE) AS score
        FROM read_parquet('{diqt}');
    """)


# ---------- Client API ----------

class DuckDBClient:
    """
    Thin wrapper exposing retrieval functions expected by the RAG pipeline.
    """

    def __init__(self, base_dir: str = "data/duckdb"):
        self.base_dir = base_dir
        self._con = init_duckdb_connection(base_dir)

    # --- DrugBank ---

    def get_synonyms(self, drug_name: str) -> List[str]:
        d = _norm_name(drug_name)
        if not d:
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
        if not a or not b:
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

    def get_dict_rank(self, drug_name: str | List[str]) -> str | Dict[str, str]:
        """Get DICT rank (severity) for a drug or list of drugs."""
        if isinstance(drug_name, list):
            return self._get_dict_rank_batch(drug_name)
        d = _norm_name(drug_name)
        if not d:
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
        if not d:
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
        if not d:
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
        if not d:
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
        if not d:
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
            # Single drug: union where A==drug or B==drug
            rows = self._con.execute(
                """
                SELECT side_effect, MAX(prr) AS prr
                FROM twosides
                WHERE (drug_a = ? OR drug_b = ?)
                  AND (prr IS NULL OR prr >= ?)
                GROUP BY side_effect
                ORDER BY COALESCE(MAX(prr), 0) DESC, side_effect
                LIMIT ?;
                """,
                [a, a, float(min_prr), int(top_k)],
            ).fetchall()
            return [r[0] for r in rows]

        # Pair (order-insensitive)
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
        if not d:
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
        if not d:
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
