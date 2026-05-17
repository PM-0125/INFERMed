#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build optional licensed DrugBank parquet data for local_dev mode.

This script must only be used when the operator has valid DrugBank license
permission. Public/demo-safe INFERMed runs do not require this file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

try:
    from lxml import etree as ET
except Exception:
    import xml.etree.ElementTree as ET
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

def convert_drugbank_xml(xml_path: str, out_parquet: str, xsd_path: Optional[str] = None) -> None:
    """
    DrugBank XML â†’ Parquet (streaming, namespace-agnostic, optional XSD validation)

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
            # very old lxml may not support '{*}tag' â€” fallback to no tag filter
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

            # Extract enzyme data (NEW) - store as enzyme->actions mapping
            # Each enzyme can have multiple actions, so we store as JSON-like structure
            # Format: [{"enzyme": "CYP3A4", "actions": ["substrate", "inhibitor"]}, ...]
            enzyme_data = []
            for e in el.xpath(".//db:enzymes/db:enzyme", namespaces=NS):
                ename = _xp(e, "./db:name/text()")
                if ename:
                    # Get enzyme actions (substrate, inhibitor, inducer) for THIS enzyme
                    eacts = e.xpath(".//db:actions/db:action/text()", namespaces=NS)
                    actions = [_norm_name_lower(a) for a in eacts if a]
                    if actions:
                        enzyme_data.append({"enzyme": _norm_name(ename), "actions": actions})
                    else:
                        # If no actions specified, still record the enzyme
                        enzyme_data.append({"enzyme": _norm_name(ename), "actions": []})

            # For backward compatibility, also store as flat lists
            enzymes = [ed["enzyme"] for ed in enzyme_data]
            enzyme_actions = []
            for ed in enzyme_data:
                enzyme_actions.extend(ed["actions"])  # Flatten all actions
            # Also store structured data as JSON string for proper mapping
            import json
            # Always create JSON string, even if empty list (for consistency)
            enzyme_action_map = json.dumps(enzyme_data) if enzyme_data else json.dumps([])

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
                "enzymes": sorted(set([e for e in enzymes if e])),  # NEW
                "enzyme_actions": sorted(set([a for a in enzyme_actions if a])),  # NEW
                "enzyme_action_map": enzyme_action_map,
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

                # Extract enzyme data with proper mapping (same as lxml path)
                enzyme_data = []
                e_nodes = el.xpath(".//db:enzymes/db:enzyme", namespaces=nsmap) or el.xpath(".//enzymes/enzyme")
                for e in e_nodes:
                    ename = _xp(e, "./db:name/text()") or (e.find("./name") is not None and e.find("./name").text)
                    if ename:
                        eacts = e.xpath(".//db:actions/db:action/text()", namespaces=nsmap) or \
                               [a.text for a in e.xpath(".//actions/action") if a is not None and a.text]
                        actions = [_norm_name_lower(a) for a in eacts if a]
                        if actions:
                            enzyme_data.append({"enzyme": _norm_name(ename), "actions": actions})
                        else:
                            enzyme_data.append({"enzyme": _norm_name(ename), "actions": []})

                enzymes = [ed["enzyme"] for ed in enzyme_data]
                enzyme_actions = []
                for ed in enzyme_data:
                    enzyme_actions.extend(ed["actions"])
                import json
                enzyme_action_map = json.dumps(enzyme_data) if enzyme_data else json.dumps([])

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
                    "enzymes": sorted(set([e for e in enzymes if e])),
                    "enzyme_actions": sorted(set([a for a in enzyme_actions if a])),
                    "enzyme_action_map": enzyme_action_map,  # Structured mapping
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

            # Extract enzyme data (NEW) - store as enzyme->actions mapping
            enzyme_data = []
            e_nodes = _findall(el, ".//{"+db_ns+"}enzymes/{"+db_ns+"}enzyme", ".//enzymes/enzyme")
            for e in e_nodes:
                en = _find(e, ".//{"+db_ns+"}name", "name")
                ename = _norm_name(en.text) if (en is not None and en.text) else None
                if ename:
                    eacts = [a.text for a in _findall(e, ".//{"+db_ns+"}action", ".//action") if a is not None and a.text]
                    actions = [_norm_name_lower(a) for a in eacts if a]
                    if actions:
                        enzyme_data.append({"enzyme": ename, "actions": actions})
                    else:
                        enzyme_data.append({"enzyme": ename, "actions": []})

            enzymes = [ed["enzyme"] for ed in enzyme_data]
            enzyme_actions = []
            for ed in enzyme_data:
                enzyme_actions.extend(ed["actions"])
            import json
            # Always create JSON string, even if empty list (for consistency)
            enzyme_action_map = json.dumps(enzyme_data) if enzyme_data else json.dumps([])

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
                "enzymes": sorted(set([e for e in enzymes if e])),  # NEW
                "enzyme_actions": sorted(set([a for a in enzyme_actions if a])),  # NEW
                "enzyme_action_map": enzyme_action_map,
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
        "targets","target_uniprot","target_actions","enzymes","enzyme_actions","enzyme_action_map","interactions"
    ]
    df = pd.DataFrame.from_records(records)
    for c in expected:
        if c not in df.columns:
            df[c] = [] if c in {"synonyms","atc_codes","targets","target_uniprot","target_actions","enzymes","enzyme_actions","interactions"} else None
    for col in ["synonyms","atc_codes","targets","target_uniprot","target_actions","enzymes","enzyme_actions","interactions"]:
        df[col] = df[col].apply(_to_list)
    # enzyme_action_map is JSON string (VARCHAR), not a list - ensure it's stored as string
    if "enzyme_action_map" not in df.columns:
        df["enzyme_action_map"] = "[]"  # Default to empty JSON array string
    else:
        # Ensure enzyme_action_map is stored as string (VARCHAR) not converted to other types
        # Replace None/NaN with empty JSON array string
        df["enzyme_action_map"] = df["enzyme_action_map"].fillna("[]").astype(str)
        df.loc[df["enzyme_action_map"] == "None", "enzyme_action_map"] = "[]"
        df.loc[df["enzyme_action_map"] == "nan", "enzyme_action_map"] = "[]"

    Path(out_parquet).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_parquet, index=False)
    print(f"[OK] DrugBank â†’ {out_parquet}  (rows={len(df)})")



def reconstruct_enzyme_action_map(enzymes, enzyme_actions) -> str:
    """Best-effort mapping for older DrugBank parquets without enzyme_action_map."""
    import json

    if not enzymes:
        return json.dumps([])
    if not isinstance(enzymes, list):
        if hasattr(enzymes, "__iter__") and not isinstance(enzymes, str):
            enzymes = list(enzymes)
        else:
            enzymes = []
    if not isinstance(enzyme_actions, list):
        if hasattr(enzyme_actions, "__iter__") and not isinstance(enzyme_actions, str):
            enzyme_actions = list(enzyme_actions)
        else:
            enzyme_actions = []

    items = []
    for index, enzyme in enumerate(enzymes):
        actions = []
        if enzyme_actions:
            actions = [enzyme_actions[min(index, len(enzyme_actions) - 1)]]
        items.append({"enzyme": str(enzyme), "actions": [str(a) for a in actions if a]})
    return json.dumps(items)


def add_enzyme_action_map(parquet_path: str, output_path: str | None = None) -> None:
    """Patch an older licensed DrugBank parquet with enzyme_action_map."""
    parquet = Path(parquet_path)
    if not parquet.exists():
        raise FileNotFoundError(f"Parquet file not found: {parquet}")
    output = Path(output_path) if output_path else parquet

    df = pd.read_parquet(parquet)
    if "enzymes" not in df.columns:
        raise ValueError("DrugBank parquet must contain an enzymes column")
    if "enzyme_actions" not in df.columns:
        df["enzyme_actions"] = [[] for _ in range(len(df))]
    if "enzyme_action_map" not in df.columns:
        df["enzyme_action_map"] = None

    def is_empty_map(value) -> bool:
        if value is None:
            return True
        text = str(value).strip()
        return text in {"", "[]", "None", "nan"}

    def as_list(value) -> list:
        try:
            if pd.isna(value):
                return []
        except (ValueError, TypeError):
            pass
        if isinstance(value, list):
            return value
        if hasattr(value, "tolist"):
            return value.tolist()
        if hasattr(value, "__iter__") and not isinstance(value, str):
            return list(value)
        return []

    needs_update = df["enzyme_action_map"].apply(is_empty_map)
    if needs_update.any():
        df.loc[needs_update, "enzyme_action_map"] = df.loc[needs_update].apply(
            lambda row: reconstruct_enzyme_action_map(as_list(row["enzymes"]), as_list(row["enzyme_actions"])),
            axis=1,
        )

    df["enzyme_action_map"] = df["enzyme_action_map"].fillna("[]").astype(str)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    print(f"[OK] patched DrugBank enzyme_action_map -> {output} (rows={len(df)})")

def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build optional licensed DrugBank parquet data. Requires valid DrugBank license/permission."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("xml", help="Convert licensed DrugBank XML to Parquet.")
    build.add_argument("--xml", required=True, help="Path to licensed DrugBank full database XML.")
    build.add_argument("--out", required=True, help="Output Parquet path, usually data/private/drugbank.parquet.")
    build.add_argument("--xsd", required=False, help="Optional DrugBank XSD for validation/namespace.")

    patch = sub.add_parser("patch-existing", help="Add enzyme_action_map to an older DrugBank parquet.")
    patch.add_argument("--parquet", required=True, help="Path to existing DrugBank parquet.")
    patch.add_argument("--out", required=False, help="Output Parquet path. Defaults to overwriting input.")

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = _build_cli().parse_args(argv)
    if args.cmd == "xml":
        convert_drugbank_xml(args.xml, args.out, getattr(args, "xsd", None))
    elif args.cmd == "patch-existing":
        add_enzyme_action_map(args.parquet, args.out)
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
