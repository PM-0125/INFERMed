#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build public-safe source datasets into tidy Parquet files for DuckDB.

Supported datasets:
  - TWOSIDES CSV -> drug_a, drug_b, side_effect, prr
  - OFFSIDES CSV -> drug_name, side_effect, prr
  - SIDER label side effects -> drug_name, side_effect, source_label
  - DICTRank Excel -> drug_name, score
  - DILIrank Excel -> drug_name, dili_score
  - DIQT Excel -> drug_name, score

DrugBank is intentionally excluded from this script. If you have valid
DrugBank license permission, use scripts/build_drugbank_parquet.py instead.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

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

def convert_offsides_csv(csv_path: str, out_parquet: str) -> None:
    """Normalize OFFSIDES to: drug_name, side_effect, prr, mean_reporting_frequency."""
    p = Path(csv_path)
    assert p.exists(), f"CSV not found: {csv_path}"

    df = pd.read_csv(p, compression="infer", low_memory=False)
    cols = {c.lower(): c for c in df.columns}
    name_col = cols.get("drug_concept_name") or cols.get("drug_name") or cols.get("drug")
    se_col = cols.get("condition_concept_name") or cols.get("side_effect") or cols.get("adverse_event")
    prr_col = cols.get("prr")
    freq_col = cols.get("mean_reporting_frequency")
    if not name_col or not se_col:
        raise ValueError(f"OFFSIDES: could not infer drug and side-effect columns from {list(df.columns)}")

    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(_norm_name_lower),
            "side_effect": df[se_col].map(_norm_name),
            "prr": pd.to_numeric(df[prr_col], errors="coerce") if prr_col else None,
            "mean_reporting_frequency": pd.to_numeric(df[freq_col], errors="coerce") if freq_col else None,
        }
    )
    for col in ("a", "b", "c", "d"):
        if col in cols:
            out[col.upper()] = pd.to_numeric(df[cols[col]], errors="coerce")
    out = out.dropna(subset=["drug_name", "side_effect"])
    out = out[(out["drug_name"] != "") & (out["side_effect"] != "")]
    out = out.drop_duplicates(subset=["drug_name", "side_effect"])
    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] OFFSIDES -> {out_parquet}  (rows={len(out)})")


def convert_sider_label_side_effects(
    drug_names_path: str,
    label_side_effects_path: str,
    out_parquet: str,
) -> None:
    """Normalize SIDER label side effects to: drug_name, stitch_id, side_effect, source_label."""
    names_p = Path(drug_names_path)
    se_p = Path(label_side_effects_path)
    assert names_p.exists(), f"SIDER drug_names.tsv not found: {drug_names_path}"
    assert se_p.exists(), f"SIDER label side-effects file not found: {label_side_effects_path}"

    names = pd.read_csv(names_p, sep="\t", header=None, names=["stitch_id", "drug_name"], dtype=str)
    name_map = dict(zip(names["stitch_id"], names["drug_name"].map(_norm_name_lower)))

    df = pd.read_csv(
        se_p,
        sep="\t",
        compression="infer",
        header=None,
        names=["source_label", "stitch_flat", "stitch_stereo", "umls_found", "meddra_type", "meddra_id", "side_effect"],
        dtype=str,
        low_memory=False,
    )
    df = df[df["meddra_type"].str.upper().eq("PT")]
    flat_names = df["stitch_flat"].map(name_map)
    stereo_names = df["stitch_stereo"].map(name_map)
    drug_names = flat_names.fillna(stereo_names)
    stitch_id = df["stitch_flat"].where(flat_names.notna(), df["stitch_stereo"])
    out = pd.DataFrame(
        {
            "drug_name": drug_names,
            "stitch_id": stitch_id,
            "side_effect": df["side_effect"].map(_norm_name),
            "source_label": df["source_label"].map(_norm_name),
            "meddra_id": df["meddra_id"].map(_norm_name),
        }
    )
    out = out.dropna(subset=["drug_name", "side_effect"])
    out = out[(out["drug_name"] != "") & (out["side_effect"] != "")]
    out = out.drop_duplicates(subset=["drug_name", "side_effect"])
    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] SIDER label SE -> {out_parquet}  (rows={len(out)})")


def _norm_nsc(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _load_nci_compound_names(compound_names_path: str) -> pd.DataFrame:
    p = Path(compound_names_path)
    assert p.exists(), f"NCI-ALMANAC compound names file not found: {compound_names_path}"
    df = pd.read_csv(p, sep="\t", header=None, names=["nsc", "drug_name"], dtype=str)
    out = pd.DataFrame(
        {
            "nsc": df["nsc"].map(_norm_nsc),
            "drug_name": df["drug_name"].map(_norm_name_lower),
            "display_name": df["drug_name"].map(_norm_name),
        }
    )
    out = out.dropna(subset=["nsc", "drug_name"])
    out = out[(out["nsc"] != "") & (out["drug_name"] != "")]
    return out.drop_duplicates(subset=["nsc", "drug_name"]).reset_index(drop=True)


def convert_nci_almanac_zip(
    zip_path: str,
    compound_names_path: str,
    out_parquet: str,
    compound_out_parquet: Optional[str] = None,
    chunksize: int = 250_000,
) -> None:
    """Normalize NCI-ALMANAC growth-screen rows to parquet for DuckDB lookup.

    Outputs:
      - nci_almanac.parquet: pair/cell-line screen rows with canonical names and scores
      - nci_almanac_compounds.parquet: NSC-to-name alias index for fast query resolution

    NCI-ALMANAC score = expected growth - observed percent growth. Larger positive
    values mean stronger-than-expected cell growth inhibition in this experimental assay.
    """
    zip_p = Path(zip_path)
    assert zip_p.exists(), f"NCI-ALMANAC zip not found: {zip_path}"

    out_p = Path(out_parquet)
    compound_out_p = Path(compound_out_parquet) if compound_out_parquet else out_p.with_name("nci_almanac_compounds.parquet")
    out_p.parent.mkdir(parents=True, exist_ok=True)
    compound_out_p.parent.mkdir(parents=True, exist_ok=True)

    compounds = _load_nci_compound_names(compound_names_path)
    compounds.to_parquet(compound_out_p, index=False)
    canonical_by_nsc: Dict[str, str] = compounds.drop_duplicates(subset=["nsc"], keep="first").set_index("nsc")["drug_name"].to_dict()
    display_by_nsc: Dict[str, str] = compounds.drop_duplicates(subset=["nsc"], keep="first").set_index("nsc")["display_name"].to_dict()

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment issue
        raise RuntimeError("pyarrow is required for chunked NCI-ALMANAC parquet conversion") from exc

    usecols = [
        "COMBODRUGSEQ",
        "SCREENER",
        "STUDY",
        "TESTDATE",
        "PANELNBR",
        "CELLNBR",
        "NSC1",
        "SAMPLE1",
        "CONCINDEX1",
        "CONC1",
        "CONCUNIT1",
        "NSC2",
        "SAMPLE2",
        "CONCINDEX2",
        "CONC2",
        "CONCUNIT2",
        "PERCENTGROWTH",
        "EXPECTEDGROWTH",
        "SCORE",
        "VALID",
        "PANEL",
        "CELLNAME",
    ]

    def normalize_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
        nsc_a = chunk["NSC1"].map(_norm_nsc)
        nsc_b = chunk["NSC2"].map(_norm_nsc)
        out = pd.DataFrame(
            {
                "combo_drug_seq": pd.to_numeric(chunk["COMBODRUGSEQ"], errors="coerce").astype("Int64"),
                "screener": chunk["SCREENER"].map(_norm_name),
                "study": chunk["STUDY"].map(_norm_name),
                "test_date": chunk["TESTDATE"].map(_norm_name),
                "panel_nbr": pd.to_numeric(chunk["PANELNBR"], errors="coerce").astype("Int64"),
                "cell_nbr": pd.to_numeric(chunk["CELLNBR"], errors="coerce").astype("Int64"),
                "drug_a": nsc_a.map(canonical_by_nsc),
                "drug_b": nsc_b.map(canonical_by_nsc),
                "drug_a_display": nsc_a.map(display_by_nsc),
                "drug_b_display": nsc_b.map(display_by_nsc),
                "nsc_a": nsc_a,
                "nsc_b": nsc_b,
                "sample_a": chunk["SAMPLE1"].map(_norm_nsc),
                "sample_b": chunk["SAMPLE2"].map(_norm_nsc),
                "conc_index_a": pd.to_numeric(chunk["CONCINDEX1"], errors="coerce").astype("Int64"),
                "conc_index_b": pd.to_numeric(chunk["CONCINDEX2"], errors="coerce").astype("Int64"),
                "conc_a": pd.to_numeric(chunk["CONC1"], errors="coerce"),
                "conc_b": pd.to_numeric(chunk["CONC2"], errors="coerce"),
                "conc_unit_a": chunk["CONCUNIT1"].map(_norm_name),
                "conc_unit_b": chunk["CONCUNIT2"].map(_norm_name),
                "percent_growth": pd.to_numeric(chunk["PERCENTGROWTH"], errors="coerce"),
                "expected_growth": pd.to_numeric(chunk["EXPECTEDGROWTH"], errors="coerce"),
                "score": pd.to_numeric(chunk["SCORE"], errors="coerce"),
                "valid": chunk["VALID"].map(_norm_name),
                "panel": chunk["PANEL"].map(_norm_name),
                "cell_name": chunk["CELLNAME"].map(_norm_name),
            }
        )
        return out.dropna(subset=["nsc_a", "nsc_b"]).reset_index(drop=True)

    rows_written = 0
    writer: Optional[pq.ParquetWriter] = None
    with zipfile.ZipFile(zip_p) as zf:
        names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"NCI-ALMANAC zip has no CSV file: {zip_path}")
        with zf.open(names[0]) as handle:
            for chunk in pd.read_csv(handle, usecols=usecols, chunksize=chunksize, low_memory=False):
                normalized = normalize_chunk(chunk)
                if normalized.empty:
                    continue
                table = pa.Table.from_pandas(normalized, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(out_p, table.schema, compression="zstd")
                writer.write_table(table)
                rows_written += len(normalized)
    if writer is not None:
        writer.close()
    else:
        pd.DataFrame(columns=["nsc_a", "nsc_b", "score"]).to_parquet(out_p, index=False)

    print(f"[OK] NCI-ALMANAC -> {out_parquet}  (rows={rows_written})")
    print(f"[OK] NCI-ALMANAC compounds -> {compound_out_p}  (rows={len(compounds)})")


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

    offsides = sub.add_parser("offsides", help="Convert OFFSIDES CSV/XZ to Parquet.")
    offsides.add_argument("--csv", required=True, help="Path to OFFSIDES.csv.xz or CSV.")
    offsides.add_argument("--out", required=True, help="Output Parquet file path.")

    sider = sub.add_parser("sider-label-se", help="Convert SIDER label side-effect TSV files to Parquet.")
    sider.add_argument("--drug-names", required=True, help="Path to SIDER drug_names.tsv.")
    sider.add_argument("--label-se", required=True, help="Path to SIDER meddra_all_label_se.tsv.gz.")
    sider.add_argument("--out", required=True, help="Output Parquet file path.")

    dictrank = sub.add_parser("dictrank", help="Convert DICTRank Excel to Parquet.")
    dictrank.add_argument("--xlsx", required=True, help="Path to dictrank_dataset_508.xlsx.")
    dictrank.add_argument("--out", required=True, help="Output Parquet file path.")

    dilirank = sub.add_parser("dilirank", help="Convert DILIrank Excel to Parquet.")
    dilirank.add_argument("--xlsx", required=True, help="Path to dilirank_diliscore_lit.xlsx.")
    dilirank.add_argument("--out", required=True, help="Output Parquet file path.")

    diqt = sub.add_parser("diqt", help="Convert DIQT Excel to tidy Parquet.")
    diqt.add_argument("--xlsx", required=True, help='Path to "diqt-drug information.xlsx".')
    diqt.add_argument("--out", required=True, help="Output Parquet file path.")

    nci = sub.add_parser("nci-almanac", help="Convert NCI-ALMANAC combo growth zip to Parquet.")
    nci.add_argument("--zip", required=True, help="Path to ComboDrugGrowth_Nov2017.zip.")
    nci.add_argument("--compound-names", required=True, help="Path to ComboCompoundNames_all.txt.")
    nci.add_argument("--out", required=True, help="Output NCI-ALMANAC Parquet file path.")
    nci.add_argument("--compound-out", default=None, help="Output compound alias Parquet path. Defaults beside --out.")
    nci.add_argument("--chunksize", type=int, default=250_000, help="CSV rows per conversion chunk.")

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = _build_cli().parse_args(argv)
    if args.cmd == "twosides":
        convert_twosides_csv(args.csv, args.out)
    elif args.cmd == "offsides":
        convert_offsides_csv(args.csv, args.out)
    elif args.cmd == "sider-label-se":
        convert_sider_label_side_effects(args.drug_names, args.label_se, args.out)
    elif args.cmd == "dictrank":
        convert_dictrank_excel(args.xlsx, args.out)
    elif args.cmd == "dilirank":
        convert_dilirank_excel(args.xlsx, args.out)
    elif args.cmd == "diqt":
        convert_diqt_excel(args.xlsx, args.out)
    elif args.cmd == "nci-almanac":
        convert_nci_almanac_zip(args.zip, args.compound_names, args.out, args.compound_out, chunksize=args.chunksize)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
