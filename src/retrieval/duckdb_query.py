import os
import duckdb
from duckdb import DuckDBPyConnection
from typing import List, Optional

# Module-level DuckDB connection
_conn: DuckDBPyConnection


def init_duckdb_connection(parquet_dir: str) -> DuckDBPyConnection:
    """
    Initialize an in-memory DuckDB connection and register Parquet files as cleaned views.

    parquet_dir should contain:
      - twosides.parquet
      - DILIrank-DILIscore.parquet
      - DICTRank.parquet
      - DIQT-Drug-Info.parquet
      - drugbank_xml.parquet

    The following views will be created:
      - twosides   (raw adverse events)
      - dilirank   (compound_name, concern)
      - dictrank   (drug_name, severity)
      - diqt       (drug_name, score)
      - drugbank   (name, interactions)
    """
    global _conn  
    # Establish connection
    _conn = duckdb.connect(database=':memory:')

    # 1) twosides raw view
    _conn.execute(f"""
        CREATE OR REPLACE TEMPORARY VIEW twosides AS
        SELECT * FROM read_parquet('{os.path.join(parquet_dir, 'twosides.parquet')}')
    """)

    # 2) DILIrank cleaned view
    dilirank_path = os.path.join(parquet_dir, 'DILIrank-DILIscore.parquet')
    _conn.execute(f"""
        CREATE OR REPLACE TEMPORARY VIEW dilirank AS
        SELECT
          "Compound Name" AS drug_name,
          vDILIConcern AS concern
        FROM read_parquet('{dilirank_path}')
    """)

    # 3) DICTRank cleaned view: coalesce multiple potential name columns
    dictrank_path = os.path.join(parquet_dir, 'DICTRank.parquet')
    _conn.execute(f"""
        CREATE OR REPLACE TEMPORARY VIEW dictrank AS
        SELECT
          COALESCE(
            "HYALURONIC ACID",
            "Hyaluronic Acid",
            "HYALURONIC ACID.1"
          ) AS drug_name,
          "Unnamed: 7" AS severity
        FROM read_parquet('{dictrank_path}')
    """)

    # 4) DIQT cleaned view
    diqt_path = os.path.join(parquet_dir, 'DIQT-Drug-Info.parquet')
    ns_name = 'Astemizole (Hismanal)'
    score_col = '2247'
    _conn.execute(f"""
        CREATE OR REPLACE TEMPORARY VIEW diqt AS
        SELECT
          "{ns_name}" AS drug_name,
          CAST("{score_col}" AS DOUBLE) AS score
        FROM read_parquet('{diqt_path}')
    """)

    # 5) DrugBank interactions view
    drugbank_path = os.path.join(parquet_dir, 'drugbank_xml.parquet')
    _conn.execute(f"""
        CREATE OR REPLACE TEMPORARY VIEW drugbank AS
        SELECT name, interactions
        FROM read_parquet('{drugbank_path}')
    """)
    return _conn


def get_side_effects(drug_name: str) -> List[str]:
    """Return unique adverse-event names where the drug appears"""
    sql = '''
        SELECT DISTINCT condition_concept_name
        FROM twosides
        WHERE lower(drug_1_concept_name)=lower(?)
           OR lower(drug_2_concept_name)=lower(?)
    '''
    rows = _conn.execute(sql, [drug_name, drug_name]).fetchall()
    return [r[0] for r in rows if r[0]]


def get_interaction_score(drug1: str, drug2: str) -> float:
    """Return PRR score for the drug pair; 0.0 if none"""
    sql = '''
        SELECT CAST(prr AS DOUBLE) AS score
        FROM twosides
        WHERE (lower(drug_1_concept_name)=lower(?) AND lower(drug_2_concept_name)=lower(?))
           OR (lower(drug_1_concept_name)=lower(?) AND lower(drug_2_concept_name)=lower(?))
        LIMIT 1
    '''
    rows = _conn.execute(sql, [drug1, drug2, drug2, drug1]).fetchall()
    return rows[0][0] if rows and rows[0][0] is not None else 0.0


def get_dili_risk(drug_name: str) -> str:
    """Map vDILIConcern to low/medium/high, or '' if not found"""
    sql = '''
        SELECT concern
        FROM dilirank
        WHERE lower(drug_name)=lower(?)
        LIMIT 1
    '''
    rows = _conn.execute(sql, [drug_name]).fetchall()
    if not rows or not rows[0][0]:
        return ''
    rc = rows[0][0].lower()
    if 'no-dili' in rc:
        return 'low'
    if 'less-dili' in rc:
        return 'medium'
    if 'most-dili' in rc:
        return 'high'
    return ''


def get_dict_rank(drug_name: str) -> str:
    rows = _conn.execute(
        "SELECT severity FROM dictrank WHERE lower(drug_name)=lower(?) LIMIT 1",
        [drug_name],
    ).fetchall()
    if rows and rows[0][0]:
        return rows[0][0].lower()
    return "unknown"


def get_diqt_score(drug_name: str) -> Optional[float]:
    """Return DIQT score or None if not found"""
    sql = '''
        SELECT score
        FROM diqt
        WHERE lower(drug_name)=lower(?)
        LIMIT 1
    '''
    rows = _conn.execute(sql, [drug_name]).fetchall()
    return rows[0][0] if rows and rows[0][0] is not None else None


def get_drug_targets(drug_name: str) -> List[str]:
    """Return list of 'id|name|description' interactions or empty list"""
    sql = '''
        SELECT interactions
        FROM drugbank
        WHERE lower(name)=lower(?)
        LIMIT 1
    '''
    rows = _conn.execute(sql, [drug_name]).fetchall()
    if not rows or not rows[0][0]:
        return []
    return [item for item in rows[0][0].split(';;') if item]
