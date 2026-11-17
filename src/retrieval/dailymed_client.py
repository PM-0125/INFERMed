from __future__ import annotations

import logging
import time
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests

from src.utils.caching import load_json, save_json
from src.retrieval.spl_units import split_text_units
from src.retrieval.spl_tables import retain_table, render_table
from src.retrieval.spl_layouts import reviewed_hierarchy

LOG = logging.getLogger(__name__)

DAILYMED_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
DEFAULT_CACHE_DIR = "data/cache/dailymed"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_TIMEOUT = 10


class DailyMedClient:
    """DailyMed v2 web-service metadata wrapper for current SPL records."""

    def __init__(
        self,
        cache_dir: str = DEFAULT_CACHE_DIR,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.timeout = int(timeout)
        self._session = requests.Session()

    def get_spl_metadata(self, drug_name: str) -> dict[str, Any]:
        name = str(drug_name or "").strip()
        if not name:
            return {"query": drug_name, "found": False, "records": []}

        key = f"dailymed__spls__{name}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached

        payload = self._request(
            "spls.json",
            {
                "drug_name": name,
                "pagesize": "5",
                "page": "1",
            },
        )
        records = self._normalize_records(payload)
        out = {
            "query": name,
            "found": bool(records),
            "records": records,
            "provenance": {
                "source": "DailyMed SPL Web Services",
                "source_url": "https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm",
                "api_base": DAILYMED_BASE,
            },
        }
        save_json(self.cache_dir, key, out)
        return out

    def get_label(self, drug_name: str) -> dict[str, Any]:
        """Retain one verified single-ingredient human label, with explicit omissions."""
        metadata = self.get_spl_metadata(drug_name)
        key = f"dailymed__passages_v8__{drug_name}"
        cached = load_json(self.cache_dir, key, ttl=self.ttl_seconds)
        if cached is not None:
            return cached
        failures = []
        for record in metadata.get("records", [])[:2]:
            setid = record.get("set_id", "")
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", setid):
                continue
            try:
                response = self._session.get(
                    f"{DAILYMED_BASE}/spls/{setid}.xml", timeout=self.timeout
                )
                response.raise_for_status()
                label = self.parse_spl(response.text, drug_name, record)
                if label.get("found"):
                    label["records"] = metadata.get("records", [])
                    save_json(self.cache_dir, key, label)
                    return label
                failures.extend(label.get("limitations", []))
            except (requests.RequestException, ET.ParseError, ValueError):
                failures.append("SPL body retrieval or XML validation failed.")
        return {
            **metadata,
            "found": False,
            "sections": {},
            "limitations": failures or ["No verified label body available."],
        }

    @staticmethod
    def parse_spl(xml, drug_name, record):
        empty = {"query": drug_name, "found": False, "sections": {}, "limitations": []}
        if len(xml) > 4_000_000 or re.search(r"<!DOCTYPE|<!ENTITY", xml, re.I):
            return {**empty, "limitations": ["Unsupported or oversized SPL document."]}
        root = ET.fromstring(xml)
        ns = {"s": "urn:hl7-org:v3"}

        def attribute(path, key):
            node = root.find(path, ns)
            return node.get(key, "") if node is not None else ""

        setid = attribute("s:setId", "root")
        version = attribute("s:versionNumber", "value")
        ingredients = {
            "".join(n.itertext()).strip().lower()
            for ingredient in root.findall(".//s:ingredient", ns)
            if ingredient.get("classCode", "").startswith("ACTI")
            for n in ingredient.findall("s:ingredientSubstance/s:name", ns)
        }
        query = drug_name.strip().lower()
        allowed = {
            query,
            query + " sodium",
            query + " hydrochloride",
            query + " potassium",
            query + " calcium",
        }
        if (
            not ingredients
            or not ingredients.issubset(allowed)
            or setid != record.get("set_id")
            or (record.get("spl_version") and version != str(record["spl_version"]))
            or attribute("s:code", "code") != "34391-3"
        ):
            return {
                **empty,
                "limitations": [
                    "SPL identity, version, single ingredient or human prescription label scope did not match."
                ],
            }
        codes = {
            "34066-1": "boxed_warning",
            "34070-3": "contraindications",
            "34071-1": "warnings",
            "43685-7": "warnings_and_cautions",
            "34073-7": "drug_interactions",
            "34090-1": "clinical_pharmacology",
            "34068-7": "dosage_and_administration",
            "42232-9": "precautions",
            "34067-9": "indications_and_usage",
            "43684-0": "use_in_specific_populations",
        }
        sections = {}
        tables = []
        source_units = []
        parents = {child: parent for parent in root.iter() for child in parent}

        def render(node):
            if node.tag.rsplit("}", 1)[-1] == "table":
                return render_table(node)
            value = node.text or ""
            for child in node:
                value += render(child) + (child.tail or "")
            if node.tag.rsplit("}", 1)[-1] in {"paragraph", "item", "br"}:
                value += "\n\n"
            return value

        def body_text(node):
            return re.sub(
                r" *\n *", "\n", re.sub(r"[ \t\r\f\v]+", " ", render(node))
            ).strip()

        for section in root.findall(".//s:section", ns):
            code = section.find("s:code", ns)
            body = section.find("s:text", ns)
            if code is None or body is None:
                continue
            title = section.find("s:title", ns)
            name = codes.get(code.get("code"))
            if not name:
                if code.get("code") != "42229-5" or title is None:
                    continue
                name = re.sub(
                    r"\W+", "_", "".join(title.itertext()).strip().lower()
                ).strip("_")
            section_tables = body.findall(".//s:table", ns)
            tables.extend(
                {"section": name, **retain_table(table)} for table in section_tables
            )

            text = body_text(body)
            if text:
                sections[name] = (
                    sections.get(name, "") + ("\n\n" if name in sections else "") + text
                )
                ancestors = []
                ancestor = parents.get(section)
                while ancestor is not None:
                    if ancestor.tag == "{urn:hl7-org:v3}section":
                        ancestors.insert(0, ancestor)
                    ancestor = parents.get(ancestor)
                context, path = [], []
                safe = True
                for ancestor in ancestors:
                    heading = ancestor.find("s:title", ns)
                    if heading is not None:
                        path.append("".join(heading.itertext()).strip())
                    parent_body = ancestor.find("s:text", ns)
                    if parent_body is not None:
                        if parent_body.find(".//s:table", ns) is not None:
                            safe = False
                        context.append(body_text(parent_body))
                path.append(
                    "".join(title.itertext()).strip() if title is not None else name
                )
                # Retain all ancestor prose, never guess which condition is disposable.
                if section_tables or not safe:
                    source_units.append(
                        {
                            "section": name,
                            "section_path": path,
                            "text": "\n\n".join(path + context + [text]),
                            "parent_context": context,
                            "content_kind": "structured_table_section",
                            "boundary_policy": "whole_section_with_typed_tables",
                        }
                    )
                else:
                    source_units.extend(
                        {"section": name, **unit}
                        for unit in split_text_units(
                            body,
                            path,
                            context,
                            heading_hierarchy=reviewed_hierarchy(body, query),
                        )
                    )
        return {
            "query": drug_name,
            "action_support_eligible": False,
            "found": bool(sections),
            "sections": sections,
            "source_units": source_units,
            "tables": tables,
            "generic_names": sorted(ingredients),
            "set_id": setid,
            "spl_version": version,
            "published_date": record.get("published_date"),
            "provenance": {
                "source": "DailyMed SPL",
                "source_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}",
                "set_id": setid,
                "spl_version": version,
            },
            "limitations": [
                "Product/formulation/route applicability is not established; these passages cannot authorize prescribing advice. DailyMed and openFDA may represent the same underlying label."
            ]
            + (
                [
                    "Tables retained with authored cell roles, spans and footnotes; interpretation and prescribing authority remain unestablished."
                ]
                if tables
                else []
            ),
        }

    def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{DAILYMED_BASE}/{path.lstrip('/')}"
        for attempt in range(3):
            try:
                time.sleep(0.15)
                response = self._session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json()
                    return data if isinstance(data, dict) else {}
                if response.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(0.5 * (2**attempt))
                    continue
                return {}
            except requests.RequestException as exc:
                LOG.debug("DailyMed request failed for %s: %s", url, exc)
                time.sleep(0.5 * (2**attempt))
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _normalize_records(payload: dict[str, Any]) -> list[dict[str, str]]:
        rows = ((payload or {}).get("data") or []) if isinstance(payload, dict) else []
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            setid = str(row.get("setid") or row.get("setId") or "").strip()
            title = str(row.get("title") or "").strip()
            if not setid and not title:
                continue
            out.append(
                {
                    "set_id": setid,
                    "title": title,
                    "spl_version": str(
                        row.get("spl_version") or row.get("splVersion") or ""
                    ).strip(),
                    "published_date": str(
                        row.get("published_date") or row.get("publishedDate") or ""
                    ).strip(),
                    "source_url": (
                        f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"
                        if setid
                        else ""
                    ),
                }
            )
        return out[:5]
