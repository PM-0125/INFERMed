from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULT_REFERENCE_DIR = "data/reference"
FDA_DDI_TABLES_PATH = Path(DEFAULT_REFERENCE_DIR) / "fda_ddi_tables.json"


def load_fda_ddi_tables(path: str | Path = FDA_DDI_TABLES_PATH) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    tables = payload.get("tables") if isinstance(payload, dict) else payload
    return tables if isinstance(tables, list) else []


def match_fda_ddi_reference(
    drug_name: str,
    *,
    path: str | Path = FDA_DDI_TABLES_PATH,
    limit: int = 12,
) -> dict[str, Any]:
    """Return FDA CYP/transporter table rows that mention the drug text."""
    name = str(drug_name or "").strip()
    if not name:
        return {"query": drug_name, "matches": []}

    token = re.escape(name.lower())
    pattern = re.compile(rf"(^|[^a-z0-9]){token}([^a-z0-9]|$)")
    matches: list[dict[str, Any]] = []
    for table in load_fda_ddi_tables(path):
        rows = table.get("rows") if isinstance(table, dict) else None
        if not isinstance(rows, list):
            continue
        title = str(table.get("title") or "FDA DDI table")
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = " ".join(str(value or "") for value in row.values()).lower()
            if not pattern.search(text):
                continue
            matches.append(
                {
                    "table_title": title,
                    "row": {str(key): str(value) for key, value in row.items()},
                    "source": "FDA Drug Development and Drug Interactions table",
                    "source_url": "https://www.fda.gov/drugs/drug-interactions-labeling/drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers",
                }
            )
            if len(matches) >= limit:
                return {"query": name, "matches": matches}

    return {"query": name, "matches": matches}
