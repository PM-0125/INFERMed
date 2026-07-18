from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.dailymed_client import DailyMedClient
from src.retrieval.openfda_label_client import OpenFDALabelClient
from src.retrieval.rxnorm_client import RxNormClient

FDA_DDI_URL = (
    "https://www.fda.gov/drugs/drug-interactions-labeling/"
    "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
)
REQUEST_HEADERS = {"User-Agent": "INFERMed research downloader (public-source cache warmer)"}

DEFAULT_DRUGS = [
    "warfarin",
    "fluconazole",
    "simvastatin",
    "clarithromycin",
    "sildenafil",
    "nitroglycerin",
    "clopidogrel",
    "omeprazole",
    "lithium",
    "hydrochlorothiazide",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download public-safe reference data and warm API caches.")
    parser.add_argument("--reference-dir", default="data/reference", help="Local directory for public reference snapshots.")
    parser.add_argument("--drug", action="append", default=[], help="Drug name to warm; can be repeated.")
    parser.add_argument("--pgx-url", default="", help="Optional verified FDA PGx page URL to snapshot.")
    parser.add_argument("--skip-reference", action="store_true", help="Skip FDA reference page downloads.")
    parser.add_argument("--skip-api-warm", action="store_true", help="Skip per-drug API cache warming.")
    args = parser.parse_args(argv)

    reference_dir = Path(args.reference_dir)
    reference_dir.mkdir(parents=True, exist_ok=True)
    drugs = list(dict.fromkeys([*(args.drug or []), *([] if args.drug else DEFAULT_DRUGS)]))

    summary: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reference_dir": str(reference_dir),
        "drugs": drugs,
        "reference_downloads": {},
        "api_warm": {},
    }

    if not args.skip_reference:
        summary["reference_downloads"]["fda_ddi_tables"] = download_fda_ddi_tables(reference_dir)
        if args.pgx_url:
            summary["reference_downloads"]["fda_pgx_page"] = download_optional_page(args.pgx_url, reference_dir / "fda_pgx_biomarkers.html")

    if not args.skip_api_warm:
        summary["api_warm"] = warm_public_api_caches(drugs)

    out = reference_dir / "public_sources_manifest.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def download_fda_ddi_tables(reference_dir: Path) -> dict[str, Any]:
    html_path = reference_dir / "fda_ddi_tables.html"
    json_path = reference_dir / "fda_ddi_tables.json"
    response = requests.get(FDA_DDI_URL, headers=REQUEST_HEADERS, timeout=30)
    response.raise_for_status()
    html = response.text
    html_path.write_text(html, encoding="utf-8")

    tables = []
    for idx, table_rows in enumerate(_extract_html_tables(html), start=1):
        if not table_rows:
            continue
        headers = table_rows[0]
        if not headers or len(set(headers)) < len(headers):
            headers = [f"column_{i + 1}" for i in range(max(len(row) for row in table_rows))]
            data_rows = table_rows
        else:
            data_rows = table_rows[1:]
        rows = []
        for raw_row in data_rows:
            row = {
                headers[i] if i < len(headers) else f"column_{i + 1}": raw_row[i] if i < len(raw_row) else ""
                for i in range(max(len(headers), len(raw_row)))
            }
            row = {str(key).strip(): " ".join(str(value).split()) for key, value in row.items()}
            if any(value for value in row.values()):
                rows.append(row)
        if rows:
            title = _guess_table_title(html, idx) or f"FDA DDI table {idx}"
            tables.append({"index": idx, "title": title, "rows": rows})

    payload = {
        "source": "FDA Drug Development and Drug Interactions: Table of Substrates, Inhibitors and Inducers",
        "source_url": FDA_DDI_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "html_path": str(html_path), "json_path": str(json_path), "table_count": len(tables)}


def _guess_table_title(html: str, index: int) -> str | None:
    marker = f"Table {index}"
    position = html.find(marker)
    if position < 0:
        return None
    snippet = " ".join(html[position : position + 220].split())
    return snippet[:160] if snippet else None


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._cell_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._cell_chunks = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            text = " ".join(unescape("".join(self._cell_chunks)).split())
            self._current_row.append(text)
            self._in_cell = False
            self._cell_chunks = []
        elif tag == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._in_row = False
            self._current_row = []
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_chunks.append(data)


def _extract_html_tables(html: str) -> list[list[list[str]]]:
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def download_optional_page(url: str, output_path: Path) -> dict[str, Any]:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
        if response.status_code != 200:
            return {"ok": False, "status_code": response.status_code, "url": url}
        output_path.write_text(response.text, encoding="utf-8")
        return {"ok": True, "path": str(output_path), "url": url}
    except requests.RequestException as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def warm_public_api_caches(drugs: list[str]) -> dict[str, Any]:
    rxnorm = RxNormClient()
    labels = OpenFDALabelClient()
    dailymed = DailyMedClient()
    out: dict[str, Any] = {}
    for drug in drugs:
        item: dict[str, Any] = {}
        try:
            item["rxnorm"] = _brief(rxnorm.resolve_drug(drug), fields=("resolved", "rxcui", "name"))
        except Exception as exc:
            item["rxnorm"] = {"ok": False, "error": str(exc)}
        try:
            label = labels.get_label(drug)
            item["openfda_label"] = {
                "found": bool(label.get("found")),
                "section_count": len(label.get("sections") or {}),
                "effective_time": label.get("effective_time"),
            }
        except Exception as exc:
            item["openfda_label"] = {"ok": False, "error": str(exc)}
        try:
            metadata = dailymed.get_spl_metadata(drug)
            item["dailymed"] = {
                "found": bool(metadata.get("found")),
                "record_count": len(metadata.get("records") or []),
            }
        except Exception as exc:
            item["dailymed"] = {"ok": False, "error": str(exc)}
        out[drug] = item
    return out


def _brief(payload: dict[str, Any], *, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: payload.get(field) for field in fields}


if __name__ == "__main__":
    raise SystemExit(main())
