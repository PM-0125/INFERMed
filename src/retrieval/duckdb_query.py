import os
import duckdb
from duckdb import DuckDBPyConnection
from typing import List, Optional, Dict, Union
from functools import lru_cache

# Module-level client
_client: Optional['DuckDBClient'] = None

class DuckDBClient:
    """
    DuckDB client that registers Parquet-backed views and provides
    both single and batch query methods without materializing tables.
    """
    # SQL templates for single-drug queries
    _sql_templates: Dict[str, str] = {
        "get_side_effects":
            """
            SELECT DISTINCT condition_concept_name
            FROM twosides
            WHERE lower(drug_1_concept_name)=lower(?)
               OR lower(drug_2_concept_name)=lower(?)
            """,
        "get_interaction_score":
            """
            SELECT CAST(prr AS DOUBLE) AS score
            FROM twosides
            WHERE (lower(drug_1_concept_name)=lower(?) AND lower(drug_2_concept_name)=lower(?))
               OR (lower(drug_1_concept_name)=lower(?) AND lower(drug_2_concept_name)=lower(?))
            LIMIT 1
            """,
        "get_dili_risk":
            """
            SELECT concern
            FROM dilirank
            WHERE lower(drug_name)=lower(?)
            LIMIT 1
            """,
        "get_dict_rank":
            """
            SELECT severity
            FROM dictrank
            WHERE lower(drug_name)=lower(?)
            LIMIT 1
            """,
        "get_diqt_score":
            """
            SELECT score
            FROM diqt
            WHERE lower(drug_name) LIKE lower(?) || '%'
            LIMIT 1
            """,
        "get_drug_targets":
            """
            SELECT interactions
            FROM drugbank
            WHERE lower(name)=lower(?)
            LIMIT 1
            """,
    }

    def __init__(self, parquet_dir: str):
        # Initialize in-memory DuckDB connection
        self._con: DuckDBPyConnection = duckdb.connect(database=":memory:")
        # Register each Parquet file as a DuckDB view
        self._register_views(parquet_dir)

    def _register_views(self, base_dir: str) -> None:
        p = os.path.join
        self._con.execute(f"""
            CREATE OR REPLACE TEMPORARY VIEW twosides AS
            SELECT * FROM read_parquet('{p(base_dir, 'twosides.parquet')}')
            """)
        self._con.execute(f"""
            CREATE OR REPLACE TEMPORARY VIEW dilirank AS
            SELECT "Compound Name" AS drug_name, vDILIConcern AS concern
            FROM read_parquet('{p(base_dir, 'DILIrank-DILIscore.parquet')}')
            """)
        self._con.execute(f"""
            CREATE OR REPLACE TEMPORARY VIEW dictrank AS
            SELECT COALESCE("HYALURONIC ACID","Hyaluronic Acid","HYALURONIC ACID.1") AS drug_name,
                   "Unnamed: 7" AS severity
            FROM read_parquet('{p(base_dir, 'DICTRank.parquet')}')
            """)
        ns_col = 'Astemizole\u00A0(Hismanal)'
        self._con.execute(f"""
            CREATE OR REPLACE TEMPORARY VIEW diqt AS
            SELECT "{ns_col}" AS drug_name,
                   CAST("2247" AS DOUBLE) AS score
            FROM read_parquet('{p(base_dir, 'DIQT-Drug-Info.parquet')}')
            """)
        self._con.execute(f"""
            CREATE OR REPLACE TEMPORARY VIEW drugbank AS
            SELECT name, interactions
            FROM read_parquet('{p(base_dir, 'drugbank_xml.parquet')}')
            """)

    def get_side_effects(self, drug_name: Union[str, List[str]]) -> Union[List[str], Dict[str, List[str]]]:
        if isinstance(drug_name, list):
            # Batch mode
            placeholders = ",".join("?" for _ in drug_name)
            sql = f"""
                SELECT lower(drug_1_concept_name) AS drug, condition_concept_name
                FROM twosides
                WHERE lower(drug_1_concept_name) IN ({placeholders})
                   OR lower(drug_2_concept_name) IN ({placeholders})
            """
            params = drug_name + drug_name
            rows = self._con.execute(sql, params).fetchall()
            result: Dict[str, List[str]] = {d: [] for d in drug_name}
            for d, se in rows:
                if se and se not in result[d]:
                    result[d].append(se)
            return result
        # Single mode
        rows = self._con.execute(
            self._sql_templates['get_side_effects'], [drug_name, drug_name]
        ).fetchall()
        return [r[0] for r in rows if r and r[0]]

    def get_interaction_score(self, drug1: str, drug2: str) -> float:
        rows = self._con.execute(
            self._sql_templates['get_interaction_score'], [drug1, drug2, drug2, drug1]
        ).fetchall()
        return rows[0][0] if rows and rows[0] and rows[0][0] is not None else 0.0

    def get_dili_risk(self, drug_name: Union[str, List[str]]) -> Union[str, Dict[str, str]]:
        if isinstance(drug_name, list):
            placeholders = ",".join("?" for _ in drug_name)
            sql = f"""
                SELECT lower(drug_name), concern
                FROM dilirank
                WHERE lower(drug_name) IN ({placeholders})
            """
            rows = self._con.execute(sql, drug_name).fetchall()
            return {d: self._map_dili(val) for d, val in rows}
        rows = self._con.execute(
            self._sql_templates['get_dili_risk'], [drug_name]
        ).fetchall()
        val = rows[0][0] if rows and rows[0] and rows[0][0] else None
        return self._map_dili(val)

    def get_dict_rank(self, drug_name: Union[str, List[str]]) -> Union[str, Dict[str, str]]:
        if isinstance(drug_name, list):
            placeholders = ",".join("?" for _ in drug_name)
            sql = f"""
                SELECT lower(drug_name), severity
                FROM dictrank
                WHERE lower(drug_name) IN ({placeholders})
            """
            rows = self._con.execute(sql, drug_name).fetchall()
            return {d: (s.lower() if s else 'unknown') for d, s in rows}
        rows = self._con.execute(
            self._sql_templates['get_dict_rank'], [drug_name]
        ).fetchall()
        return rows[0][0].lower() if rows and rows[0] and rows[0][0] else 'unknown'

    def get_diqt_score(self, drug_name: Union[str, List[str]]) -> Union[Optional[float], Dict[str, Optional[float]]]:
        if isinstance(drug_name, list):
            placeholders = ",".join("?" for _ in drug_name)
            sql = f"""
                SELECT lower(drug_name), score
                FROM diqt
                WHERE lower(drug_name) LIKE lower(?) || '%'
                  AND lower(drug_name) IN ({placeholders})
            """
            params = [drug_name[0]] + drug_name
            rows = self._con.execute(sql, params).fetchall()
            return {d: sc for d, sc in rows}
        rows = self._con.execute(
            self._sql_templates['get_diqt_score'], [drug_name]
        ).fetchall()
        return rows[0][0] if rows and rows[0] and rows[0][0] is not None else None

    def get_drug_targets(self, drug_name: Union[str, List[str]]) -> Union[List[str], Dict[str, List[str]]]:
        if isinstance(drug_name, list):
            placeholders = ",".join("?" for _ in drug_name)
            sql = f"""
                SELECT lower(name), interactions
                FROM drugbank
                WHERE lower(name) IN ({placeholders})
            """
            rows = self._con.execute(sql, drug_name).fetchall()
            return {d: inter.split(';;') if inter else [] for d, inter in rows}
        rows = self._con.execute(
            self._sql_templates['get_drug_targets'], [drug_name]
        ).fetchall()
        if not rows or not rows[0] or not rows[0][0]:
            return []
        return rows[0][0].split(';;')

    def _map_dili(self, val: Optional[str]) -> str:
        if not val:
            return ''
        rc = val.lower()
        if 'no-dili' in rc:
            return 'low'
        if 'less-dili' in rc:
            return 'medium'
        if 'most-dili' in rc:
            return 'high'
        return ''

# Module-level wrappers with LRU caching

@lru_cache(maxsize=128)
def init_duckdb_connection(parquet_dir: str) -> DuckDBPyConnection:
    """Initialize module-level client. Must be called first!"""
    global _client
    _client = DuckDBClient(parquet_dir)
    return _client._con

@lru_cache(maxsize=256)
def get_side_effects(drug_name: str) -> List[str]:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    res = _client.get_side_effects(drug_name)
    if not isinstance(res, list):
        raise TypeError('Expected single-result list for get_side_effects')
    return res

@lru_cache(maxsize=256)
def get_interaction_score(d1: str, d2: str) -> float:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    return _client.get_interaction_score(d1, d2)

@lru_cache(maxsize=256)
def get_dili_risk(drug_name: str) -> str:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    res = _client.get_dili_risk(drug_name)
    if not isinstance(res, str):
        raise TypeError('Expected single-result string for get_dili_risk')
    return res

@lru_cache(maxsize=256)
def get_dict_rank(drug_name: str) -> str:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    res = _client.get_dict_rank(drug_name)
    if not isinstance(res, str):
        raise TypeError('Expected single-result string for get_dict_rank')
    return res

@lru_cache(maxsize=256)
def get_diqt_score(drug_name: str) -> Optional[float]:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    res = _client.get_diqt_score(drug_name)
    if isinstance(res, dict):
        raise TypeError('Expected single-result float or None for get_diqt_score')
    return res

@lru_cache(maxsize=256)
def get_drug_targets(drug_name: str) -> List[str]:
    if _client is None:
        raise RuntimeError('init_duckdb_connection() must be called first')
    res = _client.get_drug_targets(drug_name)
    if not isinstance(res, list):
        raise TypeError('Expected single-result list for get_drug_targets')
    return res
