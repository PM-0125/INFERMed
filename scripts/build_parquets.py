#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Converters for source data → tidy Parquet for DuckDB.

Datasets supported:
  - DrugBank XML                 → columns: drugbank_id, name, name_lower, synonyms, atc_codes,
                                   targets (list[str]), target_uniprot (list[str]), target_actions (list[str]),
                                   interactions (list[str])
  - TwoSides CSV                 → columns: drug_a, drug_b, side_effect, prr
  - DICTRank Excel               → columns: drug_name, score
  - DILIRank Excel               → columns: drug_name, dili_score
  - DIQT Excel (often a matrix)  → columns: drug_name, score

Usage:
  python build_parquets.py drugbank --xml path/to/full.xml --out data/duckdb/drugbank.parquet [--xsd path/to/drugbank.xsd]
  python build_parquets.py twosides --csv path/to/twosides.csv --out data/duckdb/twosides.parquet
  python build_parquets.py dictrank --xlsx dictrank_dataset_508.xlsx --out data/duckdb/dictrank.parquet
  python build_parquets.py dilirank --xlsx dilirank_diliscore_lit.xlsx --out data/duckdb/dilirank.parquet
  python build_parquets.py diqt --xlsx "diqt-drug information.xlsx" --out data/duckdb/diqt.parquet
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import warnings
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

try:
    # lxml is faster and more forgiving for large DrugBank dumps
    from lxml import etree as ET
except Exception:
    import xml.etree.ElementTree as ET  # fallback (slower, less featureful)

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

def _load_drugbank_namespace_from_xsd(xsd_path: Optional[str]) -> Optional[str]:
    if not xsd_path:
        return None
    p = Path(xsd_path)
    if not p.exists():
        print(f"[warn] XSD not found at {xsd_path}; proceeding without schema.", file=sys.stderr)
        return None
    try:
        from lxml import etree as LET
        xsd_tree = LET.parse(str(p))
        root = xsd_tree.getroot()
        return root.attrib.get("targetNamespace")
    except Exception as e:
        print(f"[warn] Failed to read XSD ({xsd_path}): {e}; proceeding without schema.", file=sys.stderr)
        return None

def _maybe_validate_with_xsd(xml_path: str, xsd_path: Optional[str]) -> None:
    if not xsd_path:
        return
    try:
        from lxml import etree as LET
        xml_doc = LET.parse(xml_path)
        xsd_doc = LET.parse(xsd_path)
        schema = LET.XMLSchema(xsd_doc)
        if not schema.validate(xml_doc):
            err = schema.error_log.last_error
            print(f"[warn] XML did not validate against XSD: {err}", file=sys.stderr)
        else:
            print("[ok] XML validated against XSD.")
    except Exception as e:
        print(f"[warn] Skipping validation (problem reading/validating XSD): {e}", file=sys.stderr)

################################################################################
# DrugBank XML → Parquet
################################################################################

def convert_drugbank_xml(xml_path: str, out_parquet: str, xsd_path: Optional[str] = None) -> None:
    """
    DrugBank XML → Parquet (streaming, namespace-agnostic, optional XSD validation)

    Extracts:
      - drugbank_id, name, name_lower
      - synonyms (list[str]), atc_codes (list[str])
      - targets (list[str]), target_uniprot (list[str]), target_actions (list[str])
      - interactions (list[str])
    """
    xml_path = Path(xml_path)
    assert xml_path.exists(), f"XML not found: {xml_path}"

    db_ns = _load_drugbank_namespace_from_xsd(xsd_path) or "http://www.drugbank.ca"
    NS = {"db": db_ns}

    _maybe_validate_with_xsd(str(xml_path), xsd_path)

    # Prefer lxml; fallback to stdlib
    use_lxml = False
    try:
        from lxml import etree as LET  # noqa
        use_lxml = True
    except Exception:
        pass

    records = []

    def _append_record(rec):
        if rec["name"] or rec["drugbank_id"]:
            records.append(rec)

    if use_lxml:
        from lxml import etree as LET

        # 1) Primary streaming pass: match any-namespace <drug>
        matched = 0
        try:
            ctx = LET.iterparse(str(xml_path), events=("end",), tag="{*}drug")
        except TypeError:
            # very old lxml may not support '{*}tag' — fallback to no tag filter
            ctx = LET.iterparse(str(xml_path), events=("end",))

        for event, el in ctx:
            tag = el.tag.split('}')[-1] if isinstance(el.tag, str) else None
            if tag != "drug":
                el.clear()
                continue
            matched += 1

            def _xp(e, xp):
                try:
                    vals = e.xpath(xp, namespaces=NS)
                    return vals[0] if vals else None
                except Exception:
                    return None

            dbid = _xp(el, ".//db:drugbank-id[@primary='true']/text()") or _xp(el, ".//db:drugbank-id/text()")
            name = _xp(el, "./db:name/text()")

            syns = el.xpath(".//db:synonyms/db:synonym/text()", namespaces=NS)
            synonyms = [_norm_name(s) for s in syns if s]

            atc = el.xpath(".//db:atc-code/@code", namespaces=NS)
            atc_codes = [a for a in atc if a]

            targets, target_uniprot, target_actions = [], [], []
            for t in el.xpath(".//db:targets/db:target", namespaces=NS):
                tname = _xp(t, "./db:name/text()")
                if tname:
                    targets.append(_norm_name(tname))
                polys = t.xpath(".//db:polypeptide", namespaces=NS)
                if polys:
                    up = polys[0].get("id")
                    if up:
                        target_uniprot.append(up)
                acts = t.xpath(".//db:actions/db:action/text()", namespaces=NS)
                target_actions.extend([_norm_name_lower(a) for a in acts if a])

            ddi_names = el.xpath(".//db:drug-interactions/db:drug-interaction/db:name/text()", namespaces=NS)
            interactions = [_norm_name(x) for x in ddi_names if x]

            _append_record({
                "drugbank_id": _norm_name(dbid),
                "name": _norm_name(name),
                "name_lower": _norm_name_lower(name),
                "synonyms": sorted(set([s for s in synonyms if s])),
                "atc_codes": sorted(set([a for a in atc_codes if a])),
                "targets": sorted(set([t for t in targets if t])),
                "target_uniprot": sorted(set([u for u in target_uniprot if u])),
                "target_actions": sorted(set([a for a in target_actions if a])),
                "interactions": sorted(set([i for i in interactions if i])),
            })

            el.clear()
        try:
            del ctx
        except Exception:
            pass

        # 2) Fallback if nothing matched (edge namespaces / odd packaging)
        if matched == 0 and len(records) == 0:
            # Parse root, detect actual ns, and run a single XPath //drug
            doc = LET.parse(str(xml_path))
            root = doc.getroot()
            # try to infer ns from root tag
            actual_ns = None
            if isinstance(root.tag, str) and root.tag.startswith("{"):
                actual_ns = root.tag.split("}")[0].strip("{")
            nsmap = {"db": actual_ns or db_ns}

            drugs = doc.xpath("//db:drug", namespaces=nsmap)
            if not drugs:
                # last resort: plain //drug
                drugs = doc.xpath("//drug")

            for el in drugs:
                def _xp(e, xp):
                    try:
                        vals = e.xpath(xp, namespaces=nsmap)
                        return vals[0] if vals else None
                    except Exception:
                        return None

                dbid = _xp(el, ".//db:drugbank-id[@primary='true']/text()") or _xp(el, ".//db:drugbank-id/text()")
                name = _xp(el, "./db:name/text()") or _xp(el, "./name/text()")

                syns = el.xpath(".//db:synonyms/db:synonym/text()", namespaces=nsmap) or \
                       el.xpath(".//synonyms/synonym/text()")
                synonyms = [_norm_name(s) for s in syns if s]

                atc = el.xpath(".//db:atc-code/@code", namespaces=nsmap) or \
                      [n.get("code") for n in el.xpath(".//atc-code")]
                atc_codes = [a for a in atc if a]

                targets, target_uniprot, target_actions = [], [], []
                t_nodes = el.xpath(".//db:targets/db:target", namespaces=nsmap) or el.xpath(".//targets/target")
                for t in t_nodes:
                    tname = _xp(t, "./db:name/text()") or _xp(t, "./name/text()")
                    if tname:
                        targets.append(_norm_name(tname))
                    polys = t.xpath(".//db:polypeptide", namespaces=nsmap) or t.xpath(".//polypeptide")
                    if polys:
                        up = polys[0].get("id")
                        if up:
                            target_uniprot.append(up)
                    acts = t.xpath(".//db:actions/db:action/text()", namespaces=nsmap) or \
                           [a.text for a in t.xpath(".//actions/action") if a is not None and a.text]
                    target_actions.extend([_norm_name_lower(a) for a in acts if a])

                ddi_names = el.xpath(".//db:drug-interactions/db:drug-interaction/db:name/text()", namespaces=nsmap) or \
                            [n.text for n in el.xpath(".//drug-interactions/drug-interaction/name") if n is not None and n.text]
                interactions = [_norm_name(x) for x in ddi_names if x]

                _append_record({
                    "drugbank_id": _norm_name(dbid),
                    "name": _norm_name(name),
                    "name_lower": _norm_name_lower(name),
                    "synonyms": sorted(set([s for s in synonyms if s])),
                    "atc_codes": sorted(set([a for a in atc_codes if a])),
                    "targets": sorted(set([t for t in targets if t])),
                    "target_uniprot": sorted(set([u for u in target_uniprot if u])),
                    "target_actions": sorted(set([a for a in target_actions if a])),
                    "interactions": sorted(set([i for i in interactions if i])),
                })

            if len(records) == 0:
                root_tag = root.tag
                raise RuntimeError(
                    f"DrugBank parse matched 0 drugs. Root tag: {root_tag}. "
                    f"Namespace guessed from XSD: {db_ns}. If your file is a 'mini' or schema-only file, "
                    f"please point to the full DrugBank dump."
                )

    else:
        # stdlib fallback (already namespace-aware but slower)
        import xml.etree.ElementTree as ET_std
        ctx = ET_std.iterparse(str(xml_path), events=("end",))
        matched = 0
        for event, el in ctx:
            tag = el.tag.split('}')[-1] if isinstance(el.tag, str) else None
            if tag != "drug":
                el.clear()
                continue
            matched += 1

            def _find(e, path_ns, path_plain):
                n = e.find(path_ns)
                return n if n is not None else e.find(path_plain)

            def _findall(e, path_ns, path_plain):
                lst = e.findall(path_ns)
                return lst if lst else e.findall(path_plain)

            name_node = _find(el, ".//{"+db_ns+"}name", "name")
            name = name_node.text if (name_node is not None and name_node.text) else None

            dbid = None
            for n in _findall(el, ".//{"+db_ns+"}drugbank-id", "drugbank-id"):
                if n.get("primary") == "true" and n.text:
                    dbid = n.text
                    break
            if dbid is None:
                n0 = _find(el, ".//{"+db_ns+"}drugbank-id", "drugbank-id")
                dbid = n0.text if (n0 is not None and n0.text) else None

            synonyms = []
            for n in _findall(el, ".//{"+db_ns+"}synonym", "synonym"):
                if n is not None and n.text:
                    synonyms.append(_norm_name(n.text))

            atc_codes = []
            for n in _findall(el, ".//{"+db_ns+"}atc-code", "atc-code"):
                code = n.get("code")
                if code:
                    atc_codes.append(code)

            targets, target_uniprot, target_actions = [], [], []
            t_nodes = _findall(el, ".//{"+db_ns+"}targets/{"+db_ns+"}target", ".//targets/target")
            for t in t_nodes:
                tn = _find(t, ".//{"+db_ns+"}name", "name")
                tname = _norm_name(tn.text) if (tn is not None and tn.text) else None
                if tname:
                    targets.append(tname)
                poly = _find(t, ".//{"+db_ns+"}polypeptide", "polypeptide")
                up = poly.get("id") if (poly is not None and poly.get("id")) else None
                if up:
                    target_uniprot.append(up)
                acts = [a.text for a in _findall(t, ".//{"+db_ns+"}action", ".//action") if a is not None and a.text]
                target_actions.extend([_norm_name_lower(a) for a in acts if a])

            interactions = []
            ddi_nodes = _findall(
                el,
                ".//{"+db_ns+"}drug-interaction/{"+db_ns+"}name",
                ".//drug-interaction/name",
            )
            for n in ddi_nodes:
                if n is not None and n.text:
                    interactions.append(_norm_name(n.text))

            _append_record({
                "drugbank_id": _norm_name(dbid),
                "name": _norm_name(name),
                "name_lower": _norm_name_lower(name),
                "synonyms": sorted(set([s for s in synonyms if s])),
                "atc_codes": sorted(set([a for a in atc_codes if a])),
                "targets": sorted(set([t for t in targets if t])),
                "target_uniprot": sorted(set([u for u in target_uniprot if u])),
                "target_actions": sorted(set([a for a in target_actions if a])),
                "interactions": sorted(set([i for i in interactions if i])),
            })

            el.clear()
        try:
            del ctx
        except Exception:
            pass

        if len(records) == 0:
            raise RuntimeError(
                "DrugBank parse matched 0 drugs with stdlib parser. "
                "Please install lxml (`pip install lxml`) or verify the XML contains <drug> entries."
            )

    # Assemble DataFrame and ensure all expected columns exist
    expected = [
        "drugbank_id","name","name_lower","synonyms","atc_codes",
        "targets","target_uniprot","target_actions","interactions"
    ]
    df = pd.DataFrame.from_records(records)
    for c in expected:
        if c not in df.columns:
            df[c] = [] if c in {"synonyms","atc_codes","targets","target_uniprot","target_actions","interactions"} else None
    for col in ["synonyms","atc_codes","targets","target_uniprot","target_actions","interactions"]:
        df[col] = df[col].apply(_to_list)

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)
    print(f"[OK] DrugBank → {out_parquet}  (rows={len(df)})")


################################################################################
# TwoSides CSV → Parquet (tidy long)
################################################################################

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
    print(f"[OK] TwoSides → {out_parquet}  (rows={len(out)})")


################################################################################
# DICTRank Excel → Parquet
################################################################################

def convert_dictrank_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Normalizes to: drug_name, score
    Picks first non-empty name-like column and first numeric score-like column.
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    df = pd.read_excel(p, sheet_name=0)
    name_col = _choose_first_nonnull(df, [c for c in df.columns if re.search(r"(drug|name)", c, re.I)])
    score_col = _choose_first_nonnull(df, [c for c in df.columns if re.search(r"(score|rank|prob|dict)", c, re.I)])

    if name_col is None:
        for c in df.columns:
            if df[c].dtype == object:
                name_col = c
                break
    if score_col is None:
        for c in df.columns:
            if _is_numeric_series(df[c]):
                score_col = c
                break

    if name_col is None or score_col is None:
        raise ValueError(f"DICTRank: Could not infer columns from {list(df.columns)}")

    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(_norm_name_lower),
            "score": pd.to_numeric(df[score_col], errors="coerce"),
        }
    ).dropna(subset=["drug_name"])

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DICTRank → {out_parquet}  (rows={len(out)})")

################################################################################
# DILIRank Excel → Parquet
################################################################################

def convert_dilirank_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Normalizes to: drug_name, dili_score
    Picks first name-like and first numeric column by heuristic.
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    df = pd.read_excel(p, sheet_name=0)

    name_col = _choose_first_nonnull(df, [c for c in df.columns if re.search(r"(drug|name)", c, re.I)])
    score_col = _choose_first_nonnull(df, [c for c in df.columns if re.search(r"(dili|score|rank|severity)", c, re.I)])
    if name_col is None:
        for c in df.columns:
            if df[c].dtype == object:
                name_col = c
                break
    if score_col is None:
        for c in df.columns:
            if _is_numeric_series(df[c]):
                score_col = c
                break

    if name_col is None or score_col is None:
        raise ValueError(f"DILIRank: Could not infer columns from {list(df.columns)}")

    out = pd.DataFrame(
        {
            "drug_name": df[name_col].map(_norm_name_lower),
            "dili_score": pd.to_numeric(df[score_col], errors="coerce"),
        }
    ).dropna(subset=["drug_name"])

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DILIRank → {out_parquet}  (rows={len(out)})")

################################################################################
# DIQT Excel (wide → tidy) → Parquet
################################################################################

def convert_diqt_excel(xlsx_path: str, out_parquet: str) -> None:
    """
    Converts DIQT Excel (often wide with a 'drug name' column and many numeric columns)
    into tidy two-column parquet: drug_name, score.

    Strategy:
      - Identify the primary name column (first object-like col or one matching '(drug|name)').
      - Identify all numeric columns.
      - Melt to long format and aggregate by drug_name using MAX (conservative).
    """
    p = Path(xlsx_path)
    assert p.exists(), f"Excel not found: {xlsx_path}"

    df = pd.read_excel(p, sheet_name=0)
    if df.empty:
        raise ValueError("DIQT: empty sheet")

    name_col = _choose_first_nonnull(df, [c for c in df.columns if re.search(r"(drug|name)", c, re.I)])
    if name_col is None:
        obj_cols = [c for c in df.columns if df[c].dtype == object]
        name_col = obj_cols[0] if obj_cols else df.columns[0]

    num_cols = [c for c in df.columns if c != name_col and _is_numeric_series(df[c])]
    if not num_cols:
        for c in df.columns:
            if c != name_col:
                try:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                except Exception:
                    pass
        num_cols = [c for c in df.columns if c != name_col and _is_numeric_series(df[c])]

    if not num_cols:
        raise ValueError(f"DIQT: no numeric score columns found. Columns={list(df.columns)}")

    long = df[[name_col] + num_cols].copy()
    long[name_col] = long[name_col].map(_norm_name_lower)
    long = long.melt(id_vars=[name_col], value_vars=num_cols, var_name="metric", value_name="score")
    long = long.dropna(subset=[name_col, "score"])

    out = (
        long.groupby(name_col, as_index=False)["score"]
        .max()
        .rename(columns={name_col: "drug_name"})
    )

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_parquet, index=False)
    print(f"[OK] DIQT → {out_parquet}  (rows={len(out)})")

################################################################################
# CLI
################################################################################

def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert source datasets to tidy Parquet.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # DrugBank
    d = sub.add_parser("drugbank", help="Convert DrugBank XML to Parquet.")
    d.add_argument("--xml", required=True, help="Path to DrugBank full database XML.")
    d.add_argument("--out", required=True, help="Output Parquet file path.")
    d.add_argument("--xsd", required=False, help="Optional path to DrugBank XSD for validation/namespace.")

    # TwoSides
    t = sub.add_parser("twosides", help="Convert TwoSides CSV to Parquet.")
    t.add_argument("--csv", required=True, help="Path to TwoSides CSV.")
    t.add_argument("--out", required=True, help="Output Parquet file path.")

    # DICTRank
    r = sub.add_parser("dictrank", help="Convert DICTRank Excel to Parquet.")
    r.add_argument("--xlsx", required=True, help="Path to dictrank_dataset_508.xlsx")
    r.add_argument("--out", required=True, help="Output Parquet file path.")

    # DILIRank
    l = sub.add_parser("dilirank", help="Convert DILIRank Excel to Parquet.")
    l.add_argument("--xlsx", required=True, help="Path to dilirank_diliscore_lit.xlsx")
    l.add_argument("--out", required=True, help="Output Parquet file path.")

    # DIQT
    q = sub.add_parser("diqt", help="Convert DIQT Excel (wide) to tidy Parquet.")
    q.add_argument("--xlsx", required=True, help='Path to "diqt-drug information.xlsx"')
    q.add_argument("--out", required=True, help="Output Parquet file path.")

    return p

def main(argv: Optional[List[str]] = None) -> None:
    args = _build_cli().parse_args(argv)
    if args.cmd == "drugbank":
        convert_drugbank_xml(args.xml, args.out, getattr(args, "xsd", None))
    elif args.cmd == "twosides":
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
