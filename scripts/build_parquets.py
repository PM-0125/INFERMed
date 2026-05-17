#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build public-safe source datasets into tidy Parquet files for DuckDB.

Supported datasets:
  - TWOSIDES CSV -> drug_a, drug_b, side_effect, prr
  - DICTRank Excel -> drug_name, score
  - DILIrank Excel -> drug_name, dili_score
  - DIQT Excel -> drug_name, score

DrugBank is intentionally excluded from this script. If you have valid
DrugBank license permission, use scripts/build_drugbank_parquet.py instead.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

################################################################################
# Utilities
################################################################################
def _norm_name(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = str(s)
    s = s.replace("\xa0", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _norm_name_lower(s: Optional[str]) -> Optional[str]:
    s = _norm_name(s)
    return s.lower() if s is not None else None

def _to_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return [str(v) for v in x if pd.notna(v)]
    if isinstance(x, str):
        return [v for v in [x] if v != ""]
    return [str(x)]

def _is_numeric_series(s: pd.Series) -> bool:
    try:
        pd.to_numeric(s.dropna().head(50))
        return True
    except Exception:
        return False

def _choose_first_nonnull(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns and df[c].notna().any():
            return c
    return None

def convert_twosides_csv(csv_path: str, out_parquet: str) -> None:
    """
    Normalize TwoSides to tidy long format:
      drug_a, drug_b, side_effect, prr

    Supports common schemas, including the A/B/C/D contingency-table flavor.
    """
    p = Path(csv_path)
    assert p.exists(), f"CSV not found: {csv_path}"

    # Avoid mixed-type surprises across chunks
    df = pd.read_csv(p, compression="infer", low_memory=False)

    cols = {c.lower(): c for c in df.columns}

    # Drug name columns: prefer concept_name over IDs
    cand_a = [
        "drug_1_concept_name", "drug1_concept_name", "drug 1 concept name",
        "drug_a", "drug a", "drug1", "a", "drugname1"
    ]
    cand_b = [
        "drug_2_concept_name", "drug2_concept_name", "drug 2 concept name",
        "drug_b", "drug b", "drug2", "b", "drugname2"
    ]
    # Side effect / MedDRA PT
    cand_se = [
        "condition_concept_name", "condition concept name",
        "side_effect", "side effect", "adverse_event", "pt", "event", "reaction"
    ]
    # PRR-like numeric columns (we'll still compute from A/B/C/D if needed)
    cand_prr = ["prr", "reporting odds ratio", "ror", "information component", "ic", "score"]

    def pick(colnames):
        for k in colnames:
            if k in cols:
                return cols[k]
        return None

    ca = pick(cand_a)
    cb = pick(cand_b)
    ce = pick(cand_se)
    cp = pick(cand_prr)

    if not ca or not cb or not ce:
        raise ValueError(
            "TwoSides: could not infer required columns.\n"
            f"Have columns: {list(df.columns)}\n"
            "Need: drug_a (name), drug_b (name), side_effect (MedDRA/PT)."
        )

    out = pd.DataFrame(
        {
            "drug_a": df[ca].map(_norm_name_lower),
            "drug_b": df[cb].map(_norm_name_lower),
            "side_effect": df[ce].map(_norm_name),
        }
    )

    # PRR handling:
    # 1) If a numeric PRR column exists, use it.
    if cp is not None:
        prr_series = pd.to_numeric(df[cp], errors="coerce")
    else:
        prr_series = pd.Series([None] * len(df))

    # 2) If PRR missing or mostly NaN, try to compute from A,B,C,D: PRR = (A/(A+C)) / (B/(B+D))
    need_compute = prr_series.isna().mean() > 0.5
    have_abcd = all(k in cols for k in ["a", "b", "c", "d"])
    if need_compute and have_abcd:
        A = pd.to_numeric(df[cols["a"]], errors="coerce")
        B = pd.to_numeric(df[cols["b"]], errors="coerce")
        C = pd.to_numeric(df[cols["c"]], errors="coerce")
        D = pd.to_numeric(df[cols["d"]], errors="coerce")

        with pd.option_context("mode.use_inf_as_na", True):
            prr_calc = (A / (A + C)) / (B / (B + D))
        prr_series = prr_calc

    out["prr"] = prr_series

    # Clean rows
    out = out.dropna(subset=["drug_a", "drug_b", "side_effect"])
    out = out[(out["drug_a"] != "") & (out["drug_b"] != "") & (out["side_effect"] != "")]
    # Drop self-pairs
    out = out[out["drug_a"] != out["drug_b"]].reset_index(drop=True)

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] TwoSides â†’ {out_parquet}  (rows={len(out)})")


################################################################################
# DICTRank Excel â†’ Parquet
################################################################################

def convert_dictrank_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Normalizes to: drug_name, score
    DICTRank uses categorical severity levels (mild, moderate, severe) which we convert to numeric scores.
    Uses 'Generic/Proper Name(s)' for drug names and 'DIC Severity Level' for severity.
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    df = pd.read_excel(p, sheet_name=0)
    
    # Prefer 'Generic/Proper Name(s)' for drug name
    name_col = None
    for col in ["Generic/Proper Name(s)", "Generic/Proper Name", "drug_name", "name"]:
        if col in df.columns:
            name_col = col
            break
    if name_col is None:
        # Fallback to first object column
        for c in df.columns:
            if df[c].dtype == object:
                name_col = c
                break
    
    # Look for severity level column (categorical: mild, moderate, severe)
    severity_col = None
    for col in ["DIC Severity Level", "Severity Level", "Severity", "DICT _ Concern"]:
        if col in df.columns:
            severity_col = col
            break
    
    if name_col is None:
        raise ValueError(f"DICTRank: Could not infer name column from {list(df.columns)}")
    
    # Convert severity levels to numeric scores
    severity_map = {
        "mild": 0.1,
        "moderate": 0.4,
        "severe": 0.7,
        "less": 0.2,
        "most": 0.8,
        "no": 0.0,
    }
    
    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(_norm_name_lower),
        }
    )
    
    if severity_col and severity_col in df.columns:
        # Map categorical severity to numeric score
        severity_series = df[severity_col].astype(str).str.lower().str.strip()
        out["score"] = severity_series.map(severity_map)
        # Replace NaN with None (for parquet compatibility)
        out["score"] = out["score"].where(out["score"].notna(), None)
    else:
        # No severity column found, set all to None
        out["score"] = None
        print(f"[WARN] DICTRank: No severity column found, all scores will be None")
    
    out = out.dropna(subset=["drug_name"])

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DICTRank â†’ {out_parquet}  (rows={len(out)}, with_score={out['score'].notna().sum()})")

################################################################################
# DILIRank Excel â†’ Parquet
################################################################################

def convert_dilirank_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Normalizes to: drug_name, dili_score
    DILIRank file has headers in first row, data starts from row 2.
    Column 'Unnamed: 1' contains compound names, 'Unnamed: 2' contains severity scores (numeric).
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    # Read Excel, skip first row which contains headers
    df = pd.read_excel(p, sheet_name=0, header=0)
    
    # The file structure: first row has headers like "Compound Name", "Severity Class"
    # Actual data starts from row 1 (0-indexed)
    # Column 'Unnamed: 1' = Compound Name, 'Unnamed: 2' = Severity Class (numeric)
    
    # Check if first row contains headers
    first_row = df.iloc[0] if len(df) > 0 else None
    if first_row is not None and isinstance(first_row.iloc[1] if len(first_row) > 1 else None, str):
        # First row is headers, skip it
        df = df.iloc[1:].reset_index(drop=True)
        # Rename columns based on what we know
        if 'Unnamed: 1' in df.columns:
            df = df.rename(columns={'Unnamed: 1': 'compound_name'})
        if 'Unnamed: 2' in df.columns:
            df = df.rename(columns={'Unnamed: 2': 'severity_class'})
    
    # Find name column (should be 'compound_name' or 'Unnamed: 1' or first object column)
    name_col = None
    for col in ["compound_name", "Compound Name", "Unnamed: 1"]:
        if col in df.columns:
            name_col = col
            break
    if name_col is None:
        # Find first object column that looks like names
        for c in df.columns:
            if df[c].dtype == object and df[c].notna().sum() > 0:
                # Check if it's not a header row value
                sample = df[c].dropna().iloc[0] if df[c].notna().any() else None
                if sample and isinstance(sample, str) and sample.lower() not in ["compound name", "severity class", "label section"]:
                    name_col = c
                    break
    
    # Find score column (should be 'severity_class' or 'Unnamed: 2' or first numeric column)
    score_col = None
    for col in ["severity_class", "Severity Class", "Unnamed: 2"]:
        if col in df.columns:
            score_col = col
            break
    if score_col is None:
        # Find first numeric column
        for c in df.columns:
            if _is_numeric_series(df[c]):
                score_col = c
                break
    
    if name_col is None:
        raise ValueError(f"DILIRank: Could not infer name column from {list(df.columns)}")
    if score_col is None:
        raise ValueError(f"DILIRank: Could not infer score column from {list(df.columns)}")

    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(_norm_name_lower),
            "dili_score": pd.to_numeric(df[score_col], errors="coerce"),
        }
    )
    
    # Remove rows where drug_name is a header value
    header_values = {"compound name", "severity class", "label section", "vdiliconcern", "version"}
    out = out[~out["drug_name"].isin(header_values)]
    out = out.dropna(subset=["drug_name"])

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DILIRank â†’ {out_parquet}  (rows={len(out)}, with_score={out['dili_score'].notna().sum()})")

################################################################################
# DIQT Excel (wide â†’ tidy) â†’ Parquet
################################################################################

def convert_diqt_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Converts DIQT Excel into tidy two-column parquet: drug_name, score.
    Uses 'Generic/Proper_Name(s)' for drug names and 'Severity Score' for scores.
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    df = pd.read_excel(p, sheet_name=0)
    if df.empty:
        raise ValueError("DIQT: empty sheet")

    # Prefer 'Generic/Proper_Name(s)' for drug name
    name_col = None
    for col in ["Generic/Proper_Name(s)", "Generic/Proper Name(s)", "Generic/Proper Name", "drug_name", "name"]:
        if col in df.columns:
            name_col = col
            break
    if name_col is None:
        obj_cols = [c for c in df.columns if df[c].dtype == object]
        name_col = obj_cols[0] if obj_cols else df.columns[0]

    # Prefer 'Severity Score' column
    score_col = None
    for col in ["Severity Score", "score", "Score"]:
        if col in df.columns:
            score_col = col
            break
    
    if score_col is None:
        # Fallback: find first numeric column that's not Pubchem_ID
        num_cols = [c for c in df.columns if c != name_col and c != "Pubchem_ID" and _is_numeric_series(df[c])]
        if num_cols:
            score_col = num_cols[0]
    
    if score_col is None:
        # Last resort: try to convert columns to numeric
        for c in df.columns:
            if c != name_col and c != "Pubchem_ID":
                try:
                    numeric_series = pd.to_numeric(df[c], errors="coerce")
                    if numeric_series.notna().sum() > 0:
                        score_col = c
                        break
                except Exception:
                    pass

    if score_col is None:
        raise ValueError(f"DIQT: no numeric score columns found. Columns={list(df.columns)}")

    # Extract drug name (may have extra text in parentheses, e.g., "Astemizole (Hismanal)")
    def extract_drug_name(s):
        if pd.isna(s):
            return None
        s = str(s)
        # Remove content in parentheses if present
        s = re.sub(r'\s*\([^)]*\)', '', s)
        return _norm_name_lower(s)
    
    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(extract_drug_name),
            "score": pd.to_numeric(df[score_col], errors="coerce"),
        }
    ).dropna(subset=["drug_name", "score"])  # Only keep rows with both name and score

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DIQT â†’ {out_parquet}  (rows={len(out)}, with_score={out['score'].notna().sum()})")

################################################################################
# CLI
################################################################################


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert public-safe source datasets to tidy Parquet.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    twosides = sub.add_parser("twosides", help="Convert TWOSIDES CSV to Parquet.")
    twosides.add_argument("--csv", required=True, help="Path to TWOSIDES CSV.")
    twosides.add_argument("--out", required=True, help="Output Parquet file path.")

    dictrank = sub.add_parser("dictrank", help="Convert DICTRank Excel to Parquet.")
    dictrank.add_argument("--xlsx", required=True, help="Path to dictrank_dataset_508.xlsx.")
    dictrank.add_argument("--out", required=True, help="Output Parquet file path.")

    dilirank = sub.add_parser("dilirank", help="Convert DILIrank Excel to Parquet.")
    dilirank.add_argument("--xlsx", required=True, help="Path to dilirank_diliscore_lit.xlsx.")
    dilirank.add_argument("--out", required=True, help="Output Parquet file path.")

    diqt = sub.add_parser("diqt", help="Convert DIQT Excel to tidy Parquet.")
    diqt.add_argument("--xlsx", required=True, help='Path to "diqt-drug information.xlsx".')
    diqt.add_argument("--out", required=True, help="Output Parquet file path.")

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = _build_cli().parse_args(argv)
    if args.cmd == "twosides":
        convert_twosides_csv(args.csv, args.out)
    elif args.cmd == "dictrank":
        convert_dictrank_excel(args.xlsx, args.out)
    elif args.cmd == "dilirank":
        convert_dilirank_excel(args.xlsx, args.out)
    elif args.cmd == "diqt":
        convert_diqt_excel(args.xlsx, args.out)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
