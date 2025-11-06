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
    con.execute(f"""
        CREATE OR REPLACE VIEW drugbank AS
        SELECT 
            COALESCE(name_lower, lower(name)) AS name_lower,
            name,
            synonyms,
            targets,
            target_uniprot,
            target_actions,
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

    def get_drug_targets(self, drug_name: str) -> List[str]:
        """
        Return TRUE PD targets (proteins/genes) for a drug name.
        """
        d = _norm_name(drug_name)
        if not d:
            return []
        rows = self._con.execute(
            "SELECT targets FROM drugbank WHERE name_lower = ? LIMIT 1;",
            [d],
        ).fetchall()
        if not rows:
            return []
        # targets is a LIST<VARCHAR> in parquet; DuckDB returns Python list
        return [t for t in (rows[0][0] or []) if t]

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

    def get_dictrank_score(self, drug_name: str) -> Optional[float]:
        d = _norm_name(drug_name)
        if not d:
            return None
        rows = self._con.execute(
            "SELECT score FROM dictrank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        return float(rows[0][0]) if rows else None

    def get_dilirank_score(self, drug_name: str) -> Optional[float]:
        d = _norm_name(drug_name)
        if not d:
            return None
        rows = self._con.execute(
            "SELECT dili_score FROM dilirank WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        return float(rows[0][0]) if rows else None

    def get_diqt_score(self, drug_name: str) -> Optional[float]:
        """
        QT risk proxy score (already melted to two columns). Exact match on normalized drug name.
        """
        d = _norm_name(drug_name)
        if not d:
            return None
        rows = self._con.execute(
            "SELECT score FROM diqt WHERE drug_name = ? LIMIT 1;",
            [d],
        ).fetchall()
        return float(rows[0][0]) if rows else None

    # --- TwoSides side-effects ---

    def get_side_effects(
        self,
        drug_a: str,
        drug_b: Optional[str] = None,
        top_k: int = 20,
        min_prr: float = 1.0,
    ) -> List[Tuple[str, Optional[float]]]:
        """
        For single drug: return most frequent side effects (by PRR if available).
        For pair (A,B): return side effects observed with the pair (order-insensitive).
        """
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
                ORDER BY COALESCE(prr, 0) DESC, side_effect
                LIMIT ?;
                """,
                [a, a, float(min_prr), int(top_k)],
            ).fetchall()
            return [(r[0], (float(r[1]) if r[1] is not None else None)) for r in rows]

        # Pair (order-insensitive)
        rows = self._con.execute(
            """
            SELECT side_effect, MAX(prr) AS prr
            FROM twosides
            WHERE ((drug_a = ? AND drug_b = ?) OR (drug_a = ? AND drug_b = ?))
              AND (prr IS NULL OR prr >= ?)
            GROUP BY side_effect
            ORDER BY COALESCE(prr, 0) DESC, side_effect
            LIMIT ?;
            """,
            [a, b, b, a, float(min_prr), int(top_k)],
        ).fetchall()
        return [(r[0], (float(r[1]) if r[1] is not None else None)) for r in rows]

    # --- Batch helpers ---

    def get_side_effects_batch(
        self, drugs: Iterable[str], top_k_per_drug: int = 10, min_prr: float = 1.0
    ) -> Dict[str, List[str]]:
        """
        Batch version for single-drug side effects (used for building context quickly).
        Returns {drug: [side_effects...]}.
        FIXED: the previous code had a truncated 'result[d].' line; we append properly and dedupe.
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
            QUALIFY ROW_NUMBER() OVER (PARTITION BY drug ORDER BY COALESCE(prr,0) DESC, side_effect) <= ?
            ORDER BY drug;
            """,
            params,
        ).fetchall()

        result: Dict[str, List[str]] = {d: [] for d in normed}
        for drug, se, _prr in rows:
            # append safely
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
