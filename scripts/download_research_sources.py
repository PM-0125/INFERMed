from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import requests


REQUEST_HEADERS = {"User-Agent": "INFERMed research downloader (local research snapshots)"}
DEFAULT_ROOT = Path("data/raw")


@dataclass(frozen=True)
class DownloadItem:
    dataset: str
    name: str
    url: str
    output: str
    source_page: str
    required: bool = True


SIDER_FILES = [
    DownloadItem("sider", "README", "http://sideeffects.embl.de/media/download/README", "sider/README", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "drug_names.tsv", "http://sideeffects.embl.de/media/download/drug_names.tsv", "sider/drug_names.tsv", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "drug_atc.tsv", "http://sideeffects.embl.de/media/download/drug_atc.tsv", "sider/drug_atc.tsv", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra_all_indications.tsv.gz", "http://sideeffects.embl.de/media/download/meddra_all_indications.tsv.gz", "sider/meddra_all_indications.tsv.gz", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra_all_se.tsv.gz", "http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz", "sider/meddra_all_se.tsv.gz", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra_freq.tsv.gz", "http://sideeffects.embl.de/media/download/meddra_freq.tsv.gz", "sider/meddra_freq.tsv.gz", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra_all_label_indications.tsv.gz", "http://sideeffects.embl.de/media/download/meddra_all_label_indications.tsv.gz", "sider/meddra_all_label_indications.tsv.gz", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra_all_label_se.tsv.gz", "http://sideeffects.embl.de/media/download/meddra_all_label_se.tsv.gz", "sider/meddra_all_label_se.tsv.gz", "https://sideeffects.embl.de/download/"),
    DownloadItem("sider", "meddra.tsv.gz", "http://sideeffects.embl.de/media/download/meddra.tsv.gz", "sider/meddra.tsv.gz", "https://sideeffects.embl.de/download/"),
]


NSIDES_FILES = [
    DownloadItem(
        "nsides",
        "nsides_v0.1_release_notes.md",
        "https://raw.githubusercontent.com/tatonetti-lab/nsides-release/master/release-notes/v0.1.md",
        "nsides/nsides_v0.1_release_notes.md",
        "https://nsides.io/",
    ),
    DownloadItem(
        "offsides",
        "OFFSIDES.csv.xz",
        "https://tatonettilab-resources.s3.amazonaws.com/nsides/OFFSIDES.csv.xz",
        "nsides/OFFSIDES.csv.xz",
        "https://nsides.io/",
    ),
]


NSIDES_OPTIONAL_DUMP = DownloadItem(
    "nsides",
    "effect_nsides-2019-11-13.sql.gz",
    "https://tatonettilab-resources.s3.amazonaws.com/nsides/effect_nsides-2019-11-13.sql.gz",
    "nsides/effect_nsides-2019-11-13.sql.gz",
    "https://nsides.io/",
    required=False,
)


def _nci_attachment(filename: str, *, version: int, modified: int) -> str:
    return (
        "https://wiki.nci.nih.gov/download/attachments/338237347/"
        f"{quote(filename)}?version={version}&modificationDate={modified}&api=v2"
    )


NCI_ALMANAC_FILES = [
    DownloadItem("nci_almanac", "attachments.json", "https://wiki.nci.nih.gov/rest/api/content/338237347/child/attachment?limit=20", "nci_almanac/attachments.json", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC"),
    DownloadItem("nci_almanac", "ComboCompoundSet.sdf", _nci_attachment("ComboCompoundSet.sdf", version=1, modified=1493822360000), "nci_almanac/ComboCompoundSet.sdf", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
    DownloadItem("nci_almanac", "ComboCompoundNames_small.txt", _nci_attachment("ComboCompoundNames_small.txt", version=1, modified=1493822467000), "nci_almanac/ComboCompoundNames_small.txt", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
    DownloadItem("nci_almanac", "ComboCompoundNames_all.txt", _nci_attachment("ComboCompoundNames_all.txt", version=1, modified=1493822512000), "nci_almanac/ComboCompoundNames_all.txt", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
    DownloadItem("nci_almanac", "ComboDrugGrowth_Nov2017.zip", _nci_attachment("ComboDrugGrowth_Nov2017.zip", version=1, modified=1510057275000), "nci_almanac/ComboDrugGrowth_Nov2017.zip", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
    DownloadItem("nci_almanac", "ALMANAC_DataFields.txt", _nci_attachment("ALMANAC_DataFields.txt", version=1, modified=1513947309000), "nci_almanac/ALMANAC_DataFields.txt", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
    DownloadItem("nci_almanac", "ALMANAC Data Fields.docx", _nci_attachment("ALMANAC Data Fields.docx", version=1, modified=1513948677000), "nci_almanac/ALMANAC Data Fields.docx", "https://wiki.nci.nih.gov/display/NCIDTPdata/NCI-ALMANAC", required=False),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download local research snapshots that do not have clean per-query APIs.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Destination root. Default: data/raw")
    parser.add_argument("--dataset", action="append", choices=["sider", "nsides", "offsides", "nci-almanac", "all"], help="Dataset to download. Can be repeated. Default: all")
    parser.add_argument("--include-nsides-dump", action="store_true", help="Also download the large nSIDES MySQL dump; normally unnecessary when using OFFSIDES + existing TWOSIDES parquet.")
    parser.add_argument("--force", action="store_true", help="Redownload files even if the destination exists.")
    args = parser.parse_args(argv)

    selected = set(args.dataset or ["all"])
    items = selected_items(selected, include_nsides_dump=args.include_nsides_dump)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "note": "Local research snapshots are intentionally stored under ignored data/raw paths.",
        "items": [],
    }
    for item in items:
        manifest["items"].append(download_item(item, root=root, force=args.force))

    manifest_path = root / "research_sources_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if all(row.get("ok") or not row.get("required") for row in manifest["items"]) else 1


def selected_items(selected: set[str], *, include_nsides_dump: bool = False) -> list[DownloadItem]:
    if "all" in selected:
        selected = {"sider", "nsides", "offsides", "nci-almanac"}

    items: list[DownloadItem] = []
    if "sider" in selected:
        items.extend(SIDER_FILES)
    if "nsides" in selected:
        items.extend(NSIDES_FILES)
        if include_nsides_dump:
            items.append(NSIDES_OPTIONAL_DUMP)
    elif "offsides" in selected:
        items.extend([item for item in NSIDES_FILES if item.dataset == "offsides"])
    if "nci-almanac" in selected:
        items.extend(NCI_ALMANAC_FILES)
    return _dedupe_items(items)


def _dedupe_items(items: Iterable[DownloadItem]) -> list[DownloadItem]:
    seen: set[str] = set()
    out: list[DownloadItem] = []
    for item in items:
        if item.output in seen:
            continue
        seen.add(item.output)
        out.append(item)
    return out


def download_item(item: DownloadItem, *, root: Path, force: bool = False) -> dict[str, object]:
    output = root / item.output
    output.parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, object] = {**asdict(item), "path": str(output), "ok": False, "skipped": False}

    if output.exists() and not force:
        row.update({"ok": True, "skipped": True, "bytes": output.stat().st_size, "sha256": sha256_file(output)})
        return row

    tmp = output.with_suffix(output.suffix + ".part")
    try:
        with requests.get(item.url, headers=REQUEST_HEADERS, stream=True, timeout=60) as response:
            response.raise_for_status()
            expected_length = int(response.headers.get("Content-Length") or 0)
            sha = hashlib.sha256()
            total = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    sha.update(chunk)
                    total += len(chunk)
            if expected_length and total < expected_length:
                raise IOError(f"downloaded {total} bytes, expected at least {expected_length}")
            tmp.replace(output)
            row.update({"ok": True, "bytes": total, "sha256": sha.hexdigest(), "content_length": expected_length})
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        row.update({"ok": False, "error": str(exc)})
    return row


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
