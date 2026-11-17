"""Lossless table retention, without guessing header roles or clinical relations."""

import json
import re
import xml.etree.ElementTree as ET


def retain_table(table):
    tag = lambda node: node.tag.rsplit("}", 1)[-1]
    text = lambda node: re.sub(r"\s+", " ", "".join(node.itertext())).strip()
    parents = {child: parent for parent in table.iter() for child in parent}
    rows = []
    for row in table.iter():
        if tag(row) != "tr":
            continue
        rows.append(
            {
                "group": tag(parents[row]),
                "cells": [
                    {
                        "role": tag(cell),
                        "text": text(cell),
                        "rowspan": cell.get("rowspan", "1"),
                        "colspan": cell.get("colspan", "1"),
                        "headers": cell.get("headers"),
                        "scope": cell.get("scope"),
                        "id": cell.get("ID") or cell.get("id"),
                    }
                    for cell in row
                    if tag(cell) in {"th", "td"}
                ],
            }
        )
    return {
        "caption": next((text(n) for n in table if tag(n) == "caption"), ""),
        "rows": rows,
        "source_xml": ET.tostring(table, encoding="unicode"),
        "action_support_eligible": False,
        "interpretation_status": "not_established",
        "limitation": "Cell roles and spans are retained as authored. Implicit headers, units, footnote scope and patient applicability are not inferred.",
    }


def render_table(table):
    data = retain_table(table)
    return (
        "\n\n[Structured source table; interpretation not established]\n"
        + json.dumps(
            {"caption": data["caption"], "rows": data["rows"]}, ensure_ascii=False
        )
        + "\n\n"
    )
