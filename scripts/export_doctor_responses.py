from __future__ import annotations

import argparse
import csv
import html
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from markdown_it import MarkdownIt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "TESTCASES" / "infermed_5_drug_benchmark_100.csv"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "response"
_MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run INFERMed doctor-review test cases and export raw plus readable response packets."
    )
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="CSV or XLSX test case file.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_ROOT), help="Root output folder. A timestamped run folder is created inside it.")
    parser.add_argument("--run-dir", default="", help="Existing run directory to resume or rebuild.")
    parser.add_argument("--resume", action="store_true", help="Reuse successful raw case JSON files already present in --run-dir.")
    parser.add_argument("--retry-attempts", type=int, default=1, help="Attempts per not-yet-successful case.")
    parser.add_argument(
        "--inter-case-delay-s",
        type=int,
        default=0,
        help="Cooldown between completed case requests to reduce hosted-provider pressure.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="FastAPI base URL.")
    parser.add_argument("--limit", type=int, default=2, help="Maximum cases to run.")
    parser.add_argument("--case-id", action="append", default=[], help="Run only a specific Case_ID. Can be repeated.")
    parser.add_argument("--audience", default="doctor", choices=["doctor", "patient", "pv_research"])
    parser.add_argument("--refresh-evidence", action="store_true", help="Force fresh evidence retrieval.")
    parser.add_argument("--timeout-s", type=int, default=900, help="Timeout per case.")
    parser.add_argument("--wait-api-s", type=int, default=120, help="How long to wait for the API before running each case.")
    parser.add_argument("--patient-context-json", default="", help="JSON object applied to every case.")
    parser.add_argument("--pdf", action="store_true", help="Also try to render doctor_review.pdf with Python Playwright if installed.")
    args = parser.parse_args()

    patient_context = _parse_patient_context(args.patient_context_json)
    cases = _load_cases(Path(args.cases))
    if args.case_id:
        selected = {case_id.strip() for case_id in args.case_id if case_id.strip()}
        cases = [case for case in cases if case.get("case_id") in selected]
    if args.limit >= 0:
        cases = cases[: args.limit]
    if not cases:
        print(json.dumps({"ok": False, "error": "No cases selected."}, indent=2))
        return 2

    run_dir = Path(args.run_dir) if args.run_dir else Path(args.output_dir) / datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for stale_error in raw_dir.glob("*.error.json"):
        stale_error.unlink(missing_ok=True)

    readiness = _get_json(f"{args.api_url.rstrip('/')}/api/readiness", timeout_s=20)
    (run_dir / "readiness.json").write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_file = raw_dir / f"{_safe_name(case['case_id'])}.json"
        if args.resume and case_file.exists():
            existing_row = _result_row_from_raw(case_file, run_dir)
            if _result_is_usable(existing_row.get("result") or {}):
                print(f"[{index}/{len(cases)}] Reusing {case['case_id']}: {', '.join(case['drugs'])}", flush=True)
                results.append(existing_row)
                continue
            print(f"[{index}/{len(cases)}] Re-running {case['case_id']} because the saved output is a provider fallback.", flush=True)
            case_file.unlink(missing_ok=True)

        print(f"[{index}/{len(cases)}] Running {case['case_id']}: {', '.join(case['drugs'])}", flush=True)
        if not _wait_for_api(args.api_url, timeout_s=args.wait_api_s):
            failure = {
                "ok": False,
                "case": case,
                "audience": args.audience,
                "patient_context": case.get("patient_context") or patient_context,
                "elapsed_s": 0,
                "error": f"API was not reachable after {args.wait_api_s}s; stopping run to avoid bulk connection-error output.",
            }
            results.append(failure)
            break
        started = time.monotonic()
        try:
            result, progress = _run_case_with_retries(
                attempts=max(args.retry_attempts, 1),
                api_url=args.api_url,
                case=case,
                audience=args.audience,
                patient_context=case.get("patient_context") or patient_context,
                refresh_evidence=args.refresh_evidence,
                timeout_s=args.timeout_s,
            )
            elapsed_s = round(time.monotonic() - started, 1)
            raw_payload = {
                "case": case,
                "audience": args.audience,
                "patient_context": case.get("patient_context") or patient_context,
                "elapsed_s": elapsed_s,
                "progress": progress,
                "result": result,
            }
            case_file.write_text(json.dumps(raw_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            display = _display_result(result)
            results.append(
                {
                    "ok": True,
                    "case": case,
                    "audience": args.audience,
                    "patient_context": case.get("patient_context") or patient_context,
                    "elapsed_s": elapsed_s,
                    "progress": progress,
                    "result": result,
                    "display": display,
                    "raw_file": str(case_file.relative_to(run_dir)),
                }
            )
        except Exception as exc:
            elapsed_s = round(time.monotonic() - started, 1)
            failure = {
                "ok": False,
                "case": case,
                "audience": args.audience,
                "patient_context": case.get("patient_context") or patient_context,
                "elapsed_s": elapsed_s,
                "error": str(exc),
            }
            results.append(failure)

        if index < len(cases) and args.inter_case_delay_s > 0:
            delay_s = max(args.inter_case_delay_s, 0)
            print(f"  waiting {delay_s}s before the next case", flush=True)
            time.sleep(delay_s)

    summary = {
        "ok": all(row.get("ok") for row in results),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_url": args.api_url,
        "audience": args.audience,
        "case_count": len(results),
        "success_count": sum(1 for row in results if row.get("ok")),
        "failure_count": sum(1 for row in results if not row.get("ok")),
        "run_dir": str(run_dir),
        "readiness": readiness,
        "cases": [
            {
                "case_id": row["case"]["case_id"],
                "ok": row.get("ok"),
                "elapsed_s": row.get("elapsed_s"),
                "risk": ((row.get("display") or {}).get("risk") or {}).get("label"),
                "error": row.get("error"),
            }
            for row in results
        ],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "responses.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in results),
        encoding="utf-8",
    )
    markdown = _render_markdown(summary, results)
    html_doc = _render_html(summary, results)
    packet = _packet_config(args.audience)
    (run_dir / packet["markdown_filename"]).write_text(markdown, encoding="utf-8")
    html_path = run_dir / packet["html_filename"]
    html_path.write_text(html_doc, encoding="utf-8")

    pdf_status = None
    if args.pdf:
        pdf_status = _try_render_pdf(html_path, run_dir / packet["pdf_filename"])
        summary["pdf"] = pdf_status
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"ok": summary["ok"], "run_dir": str(run_dir), "pdf": pdf_status}, indent=2), flush=True)
    return 0 if summary["ok"] else 1


def _load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Case file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_case_from_row(row, index) for index, row in enumerate(csv.DictReader(handle), start=1)]
    if suffix in {".xlsx", ".xls"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("Reading XLSX requires pandas/openpyxl in the project venv.") from exc
        workbook = pd.ExcelFile(path)
        cases: list[dict[str, Any]] = []
        for sheet_name in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet_name)
            columns = {str(column) for column in frame.columns}
            if "Case_ID" not in columns and not any(column.startswith("Drug_") for column in columns):
                continue
            for row in frame.to_dict("records"):
                case = _case_from_row({str(k): v for k, v in row.items()}, len(cases) + 1)
                if len(case["drugs"]) >= 2:
                    case["sheet"] = sheet_name
                    cases.append(case)
        return cases
    raise ValueError(f"Unsupported case file type: {path.suffix}")


def _case_from_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    drugs = _extract_drugs(row)
    patient_context = _patient_context_from_row(row)
    return {
        "case_id": _clean(row.get("Case_ID")) or f"case_{index:03d}",
        "drugs": drugs,
        "case_group": _clean(row.get("Case_Group")),
        "knowledge_status": _clean(row.get("Knowledge_Status")),
        "primary_domain": _clean(row.get("Primary_Domain")),
        "expected_risk_clusters": _clean(row.get("Expected_Risk_Clusters")),
        "expected_severity_band": _clean(row.get("Expected_Severity_Band")),
        "key_context_modifiers": _clean(row.get("Key_Context_Modifiers")),
        "expected_behavior": _clean(row.get("Expected_System_Behavior")),
        "evaluation_target": _clean(row.get("Evaluation_Target")),
        "source_locator": _clean(row.get("Source_Locator")),
        "source_url_1": _clean(row.get("Source_URL_1")),
        "source_url_2": _clean(row.get("Source_URL_2")),
        "ground_truth_status": _clean(row.get("Ground_Truth_Status")),
        "patient_context": patient_context,
    }


def _extract_drugs(row: dict[str, Any]) -> list[str]:
    drugs = [
        _clean(row.get(f"Drug_{idx}"))
        for idx in range(1, 16)
    ]
    drugs = [drug for drug in drugs if drug]
    if drugs:
        return drugs

    for column in ("Drugs", "Drug_List", "Medication_List", "Medications", "Combination", "Drug_Combination"):
        raw = _clean(row.get(column))
        if raw:
            parts = raw.replace(" and ", ",").replace("+", ",").replace(";", ",").split(",")
            return [part.strip() for part in parts if part.strip()]
    return []


def _patient_context_from_row(row: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "age": ("Age", "Patient_Age"),
        "sex": ("Sex", "Patient_Sex"),
        "renal_function": ("Renal_Function", "Renal", "eGFR"),
        "hepatic_function": ("Hepatic_Function", "Hepatic"),
        "qt_risk": ("QT_Risk", "QT"),
        "inr": ("INR",),
        "pregnancy": ("Pregnancy",),
        "comorbidities": ("Comorbidities",),
        "notes": ("Patient_Context", "Clinical_Context", "Notes"),
    }
    context: dict[str, Any] = {}
    for target, candidates in mapping.items():
        for column in candidates:
            value = _clean(row.get(column))
            if value:
                context[target] = value
                break
    return context


def _result_row_from_raw(case_file: Path, run_dir: Path) -> dict[str, Any]:
    payload = json.loads(case_file.read_text(encoding="utf-8"))
    result = payload.get("result") or {}
    return {
        "ok": True,
        "case": payload.get("case") or {},
        "audience": payload.get("audience"),
        "patient_context": payload.get("patient_context") or {},
        "elapsed_s": payload.get("elapsed_s"),
        "progress": payload.get("progress") or [],
        "result": result,
        "display": _display_result(result),
        "raw_file": str(case_file.relative_to(run_dir)),
    }


def _run_case_with_retries(
    *,
    attempts: int,
    api_url: str,
    case: dict[str, Any],
    audience: str,
    patient_context: dict[str, Any],
    refresh_evidence: bool,
    timeout_s: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if attempts > 1:
            print(f"  attempt {attempt}/{attempts}", flush=True)
        try:
            return _run_case(
                api_url=api_url,
                case=case,
                audience=audience,
                patient_context=patient_context,
                refresh_evidence=refresh_evidence,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            last_error = exc
            print(f"  attempt {attempt} failed: {exc}", flush=True)
            if attempt < attempts:
                backoff_s = min(60, 20 * attempt)
                print(f"  waiting {backoff_s}s before the next provider attempt", flush=True)
                time.sleep(backoff_s)
    raise RuntimeError(str(last_error) if last_error else "case failed")


def _wait_for_api(api_url: str, *, timeout_s: int) -> bool:
    deadline = time.monotonic() + max(timeout_s, 1)
    readiness_url = f"{api_url.strip().rstrip('/')}/api/readiness"
    while time.monotonic() < deadline:
        readiness = _get_json(readiness_url, timeout_s=10)
        if readiness.get("ready") is True:
            return True
        time.sleep(3)
    return False


def _run_case(
    *,
    api_url: str,
    case: dict[str, Any],
    audience: str,
    patient_context: dict[str, Any],
    refresh_evidence: bool,
    timeout_s: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(case["drugs"]) < 2:
        raise ValueError(f"{case['case_id']} has fewer than two drugs.")
    body = {
        "medications": [{"text": drug} for drug in case["drugs"]],
        "audience": audience,
        "patient_context": patient_context or None,
        "refresh_evidence": refresh_evidence,
        "analysis_depth": "standard",
    }
    request = Request(
        f"{api_url.strip().rstrip('/')}/api/medication-sets/analyze/stream",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:
            content = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach API at {api_url}: {exc}") from exc

    events = _parse_sse(content)
    progress = [event for event in events if event.get("type") == "progress"]
    errors = [event for event in events if event.get("type") == "error"]
    if errors:
        raise RuntimeError(str(errors[-1].get("detail") or errors[-1]))
    results = [event.get("result") for event in events if event.get("type") == "result" and event.get("result")]
    if not results:
        raise RuntimeError("Stream ended without a result event.")
    result = results[-1]
    if not _result_is_usable(result):
        raise RuntimeError("Model returned a provider fallback instead of a usable doctor response.")
    return result, progress


def _parse_sse(content: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in content.replace("\r\n", "\n").split("\n\n"):
        for line in chunk.split("\n"):
            if line.strip().startswith("data:"):
                raw = line.split("data:", 1)[1].strip()
                if raw:
                    events.append(json.loads(raw))
                break
    return events


def _display_result(result: dict[str, Any]) -> dict[str, Any]:
    compatibility = result.get("compatibility") if isinstance(result.get("compatibility"), dict) else {}
    display = result if result.get("assessment") else compatibility
    return {
        "analysis_id": result.get("analysisId") or result.get("analysis_id"),
        "drugs": display.get("drugs") or [],
        "risk": display.get("risk") or {},
        "assessment": display.get("assessment") or [],
        "evidence": display.get("evidence") or {},
        "executed_pairs": result.get("executedPairs") or result.get("executed_pairs") or [],
        "ndrug_reasoning": result.get("ndrugReasoning") or result.get("ndrug_reasoning") or {},
        "decision": result.get("decision") or {},
        "safety_report": result.get("safetyReport") or result.get("safety_report") or {},
        "response_provenance": result.get("response_provenance") or {},
    }


def _result_is_usable(result: dict[str, Any]) -> bool:
    text = _result_assessment_text(result)
    if len(text) < 800:
        return False
    lowered = text.lower()
    fallback_markers = (
        "unable to generate a full explanation",
        "nvidia provider error",
        "provider timed out",
        "hosted model did not respond",
        "empty visible answer",
        "no generated answer was returned",
        "request exception",
        "winerror",
    )
    return not any(marker in lowered for marker in fallback_markers)


def _result_assessment_text(result: dict[str, Any]) -> str:
    display = result if result.get("assessment") else (result.get("compatibility") or {})
    parts = []
    assessment = display.get("assessment") or []
    if isinstance(assessment, dict):
        assessment = [assessment]
    for section in assessment:
        if isinstance(section, dict):
            parts.append(_clean(section.get("body")))
    return "\n".join(part for part in parts if part)


def _render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    packet = _packet_config(str(summary.get("audience") or "doctor"))
    lines = [
        f"# {packet['title']}",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- API URL: `{summary['api_url']}`",
        f"- Cases: `{summary['case_count']}`",
        f"- Successful: `{summary['success_count']}`",
        f"- Failed: `{summary['failure_count']}`",
        "",
        "> Research-use review packet. This output is informational and intended for expert evaluation, not direct medical advice.",
        "",
    ]
    for row in rows:
        case = row["case"]
        lines.extend(["---", "", f"## {case['case_id']}: {' + '.join(case['drugs'])}", ""])
        display = row.get("display") or {}
        provenance = display.get("response_provenance") or {}
        if row.get("ok") and provenance:
            lines.extend(
                [
                    "### Provider",
                    "",
                    f"**{provenance.get('label') or provenance.get('provider') or 'Unknown'}**",
                    "",
                    provenance.get("note") or "",
                    "",
                ]
            )
        lines.extend(_case_metadata_markdown(case, row))
        if not row.get("ok"):
            lines.extend(["", "### Run Error", "", str(row.get("error") or "Unknown error"), ""])
            continue
        display = row["display"]
        risk = display["risk"]
        lines.extend(
            [
                "",
                "### System Summary",
                "",
                f"- Analysis ID: `{display.get('analysis_id') or 'not returned'}`",
                f"- Runtime: `{row.get('elapsed_s')}s`",
                f"- Risk: **{risk.get('label') or 'Unknown'}**",
                f"- Confidence: {risk.get('confidence') or 'Unknown'}",
                f"- Interaction class: {risk.get('interactionClass') or 'Unknown'}",
                f"- Executed pairs: `{len(display.get('executed_pairs') or [])}`",
                "",
                "### Agent Response",
                "",
            ]
        )
        for section in display["assessment"]:
            title = _clean(section.get("title")) or "Assessment"
            body = _clean(section.get("body"))
            lines.extend([f"#### {title}", "", body or "No text returned.", ""])
        lines.extend(["### Evidence Basis", ""])
        lines.extend(_evidence_markdown(display.get("evidence") or {}))
        progress = row.get("progress") or []
        if progress:
            lines.extend(["", "### Lifecycle Progress", ""])
            for event in progress:
                lines.append(f"- {event.get('message') or event.get('stage')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _case_metadata_markdown(case: dict[str, Any], row: dict[str, Any]) -> list[str]:
    lines = [
        "### Test Case Context",
        "",
        f"- Case group: {case.get('case_group') or 'not specified'}",
        f"- Knowledge status: {case.get('knowledge_status') or 'not specified'}",
        f"- Primary domain: {case.get('primary_domain') or 'not specified'}",
        f"- Expected risk clusters: {case.get('expected_risk_clusters') or 'not specified'}",
        f"- Expected severity band: {case.get('expected_severity_band') or 'not specified'}",
        f"- Key context modifiers: {case.get('key_context_modifiers') or 'not specified'}",
        f"- Evaluation target: {case.get('evaluation_target') or 'not specified'}",
        f"- Ground truth status: {case.get('ground_truth_status') or 'not specified'}",
    ]
    context = row.get("patient_context") or {}
    if context:
        lines.append(f"- Patient context: `{json.dumps(context, ensure_ascii=False)}`")
    return lines


def _evidence_markdown(evidence: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, label in [
        ("overview", "Overview"),
        ("openfda", "OpenFDA"),
        ("internal", "Internal"),
        ("mechanisms", "Mechanisms"),
    ]:
        card = evidence.get(key) or {}
        lines.extend([f"#### {label}", ""])
        metrics = card.get("metrics") or []
        if metrics:
            for metric in metrics:
                lines.append(f"- {metric.get('label')}: **{metric.get('value')}**")
            lines.append("")
        rows = card.get("rows") or []
        if rows:
            for item in rows:
                meta = f" ({item.get('meta')})" if item.get("meta") else ""
                lines.append(f"- **{item.get('title')}{meta}:** {item.get('description')}")
        else:
            lines.append("- No rows returned.")
        caveat = card.get("caveat")
        if caveat:
            lines.extend(["", f"> {caveat}"])
        lines.append("")
    sources = evidence.get("sources") or []
    if sources:
        lines.extend(["#### Sources", ""])
        for source in sources:
            lines.append(f"- {source.get('name')} - {source.get('state')}: {source.get('detail')}")
    return lines


def _render_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    packet = _packet_config(str(summary.get("audience") or "doctor"))
    body = []
    for row in rows:
        case = row["case"]
        body.append(f"<section class='case'><h2>{_h(case['case_id'])}: {_h(' + '.join(case['drugs']))}</h2>")
        display = row.get("display") or {}
        provenance = display.get("response_provenance") or {}
        if row.get("ok") and provenance:
            body.append(
                "<aside class='provider-banner'>"
                "<span>Provider</span>"
                f"<strong>{_h(provenance.get('label') or provenance.get('provider') or 'Unknown')}</strong>"
                f"<p>{_h(provenance.get('note') or '')}</p>"
                "</aside>"
            )
        body.append("<div class='meta-grid'>")
        for label, value, pdf_excluded in [
            ("Case group", case.get("case_group"), False),
            ("Knowledge status", case.get("knowledge_status"), True),
            ("Primary domain", case.get("primary_domain"), False),
            ("Expected risk clusters", case.get("expected_risk_clusters"), False),
            ("Expected severity band", case.get("expected_severity_band"), False),
            ("Context modifiers", case.get("key_context_modifiers"), False),
            ("Evaluation target", case.get("evaluation_target"), True),
            ("Ground truth status", case.get("ground_truth_status"), True),
        ]:
            css_class = " class='pdf-excluded'" if pdf_excluded else ""
            body.append(f"<div{css_class}><span>{_h(label)}</span><p>{_h(value or 'not specified')}</p></div>")
        if row.get("patient_context"):
            body.append(f"<div><span>Patient context</span><p>{_h(json.dumps(row['patient_context'], ensure_ascii=False))}</p></div>")
        body.append("</div>")
        if not row.get("ok"):
            body.append(f"<div class='error'>Run error: {_h(row.get('error') or 'Unknown error')}</div></section>")
            continue
        display = row["display"]
        risk = display["risk"]
        body.append(
            "<div class='system-summary'>"
            f"<div><span>Risk</span><strong>{_h(risk.get('label') or 'Unknown')}</strong></div>"
            f"<div><span>Confidence</span><strong>{_h(risk.get('confidence') or 'Unknown')}</strong></div>"
            f"<div class='pdf-excluded'><span>Class</span><strong>{_h(risk.get('interactionClass') or 'Unknown')}</strong></div>"
            f"<div class='pdf-excluded'><span>Runtime</span><strong>{_h(str(row.get('elapsed_s')))}s</strong></div>"
            "</div>"
        )
        body.append("<h3>Agent Response</h3>")
        for section in display["assessment"]:
            body.append(
                f"<article class='answer-section'><h4>{_h(section.get('title') or 'Assessment')}</h4>"
                f"<div class='answer-content'>{_markdown_html(section.get('body'))}</div></article>"
            )
        body.append("<h3>Evidence Basis</h3>")
        body.append(_evidence_html(display.get("evidence") or {}))
        body.append("</section>")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_h(packet['title'])}</title>
<style>
  :root {{
    color: #082f49;
    background: #eef7fb;
    font-family: Inter, Segoe UI, Arial, sans-serif;
  }}
  body {{ margin: 0; padding: 32px; }}
  .cover, .case {{
    background: #fff;
    border: 1px solid #c7dfea;
    border-radius: 18px;
    box-shadow: 0 12px 35px rgba(12, 74, 110, .10);
    margin: 0 auto 24px;
    max-width: 1120px;
    padding: 28px;
  }}
  h1, h2, h3, h4 {{ margin: 0 0 12px; color: #082f49; }}
  h1 {{ font-size: 34px; }}
  h2 {{ border-bottom: 3px solid #0891b2; padding-bottom: 12px; }}
  h3 {{ margin-top: 26px; color: #075985; }}
  p, li {{ color: #334155; line-height: 1.55; }}
  .notice {{ background: #fff7ed; border: 1px solid #fdba74; border-radius: 12px; padding: 14px; color: #9a3412; }}
  .provider-banner {{
    background: #ecfeff;
    border: 1px solid #67e8f9;
    border-left: 4px solid #0891b2;
    border-radius: 10px;
    margin: 12px 0;
    padding: 10px 12px;
  }}
  .provider-banner strong {{ display: block; margin-top: 3px; }}
  .provider-banner p {{ font-size: 12px; margin: 4px 0 0; }}
  .meta-grid, .system-summary {{
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }}
  .meta-grid div, .system-summary div, .evidence-card, .answer-section {{
    border: 1px solid #d6e8f0;
    border-radius: 14px;
    padding: 14px;
    background: linear-gradient(145deg, #ffffff, #f6fbfd);
  }}
  span {{ display: block; color: #0284c7; font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
  strong {{ color: #0f172a; }}
  .answer-section {{ padding: 18px 20px; }}
  .answer-section > h4 {{ border-bottom: 1px solid #d6e8f0; font-size: 17px; padding-bottom: 9px; }}
  .answer-content > :first-child {{ margin-top: 0; }}
  .answer-content > :last-child {{ margin-bottom: 0; }}
  .answer-content p {{ margin: .45rem 0 .75rem; }}
  .answer-content ul, .answer-content ol {{ margin: .45rem 0 .85rem; padding-left: 1.35rem; }}
  .answer-content li {{ margin: .3rem 0; padding-left: .18rem; }}
  .answer-content li > p {{ margin: .2rem 0; }}
  .answer-content h1, .answer-content h2, .answer-content h3,
  .answer-content h4, .answer-content h5, .answer-content h6 {{
    border-bottom: 1px solid #dcebf2;
    color: #075985;
    font-size: 15px;
    margin: 1.05rem 0 .5rem;
    padding-bottom: .3rem;
  }}
  .answer-content strong {{ color: #0f172a; font-weight: 750; }}
  .answer-content em {{ color: #475569; }}
  .answer-content blockquote {{ border-left: 3px solid #0891b2; margin: .75rem 0; padding: .1rem 0 .1rem .85rem; }}
  .evidence-grid {{ display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .evidence-card {{ padding: 16px 18px; }}
  .evidence-card > h4 {{ border-bottom: 2px solid #bae6fd; color: #075985; padding-bottom: 8px; }}
  .evidence-metrics {{ display: grid; gap: 8px; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 12px; }}
  .evidence-metric {{ background: #eff8fc; border: 1px solid #d6e8f0; border-radius: 8px; padding: 8px 10px; }}
  .evidence-metric strong {{ display: block; font-size: 13px; margin-top: 3px; }}
  .evidence-row {{ border-top: 1px solid #dcebf2; padding: 9px 0 5px; }}
  .evidence-row:first-child {{ border-top: 0; }}
  .evidence-row h5 {{ color: #0f172a; font-size: 13px; margin: 0 0 4px; }}
  .evidence-row h5 small {{ color: #64748b; font-size: 11px; font-weight: 500; }}
  .evidence-row p, .evidence-empty {{ margin: 0; }}
  .evidence-caveat {{ background: #fff7ed; border-left: 3px solid #f59e0b; color: #92400e; margin: 10px 0 0; padding: 8px 10px; }}
  .evidence-references {{ grid-column: 1 / -1; }}
  .error {{ background: #fee2e2; border: 1px solid #fca5a5; color: #991b1b; border-radius: 12px; padding: 14px; }}
  .print-only {{ display: none; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .cover, .case {{ box-shadow: none; border-radius: 0; }}
    .case {{ break-before: page; }}
    .pdf-excluded, .screen-only {{ display: none !important; }}
    .print-only {{ display: inline; }}
    h3 {{ margin-top: 14px; }}
    .meta-grid, .system-summary {{
      display: grid;
      gap: 0 24px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin: 8px 0 12px;
    }}
    .meta-grid > div, .system-summary > div {{
      align-items: start;
      background: none;
      border: 0;
      border-bottom: 1px solid #dcebf2;
      border-radius: 0;
      display: grid;
      grid-template-columns: minmax(110px, .8fr) minmax(0, 1.4fr);
      padding: 5px 0 5px 16px;
      position: relative;
    }}
    .meta-grid > div::before, .system-summary > div::before {{
      color: #0891b2;
      content: "\\2022";
      font-size: 15px;
      left: 2px;
      line-height: 1;
      position: absolute;
      top: 7px;
    }}
    .meta-grid p {{ margin: 0; }}
    .answer-section {{ break-inside: auto; margin-bottom: 6px; padding: 10px 12px; }}
    .answer-section > h4 {{ margin-bottom: 7px; padding-bottom: 6px; }}
    .answer-content p {{ margin: .25rem 0 .45rem; }}
    .answer-content ul, .answer-content ol {{ margin: .25rem 0 .5rem; }}
    .answer-content li {{ margin: .18rem 0; }}
    .answer-content h1, .answer-content h2, .answer-content h3,
    .answer-content h4, .answer-content h5, .answer-content h6 {{ margin: .65rem 0 .35rem; }}
    .evidence-grid {{ display: block; }}
    .evidence-card {{ break-inside: auto; margin-bottom: 7px; padding: 10px 12px; }}
    .evidence-card > h4 {{ margin-bottom: 7px; padding-bottom: 6px; }}
    .evidence-metrics {{ gap: 6px; margin-bottom: 7px; }}
    .evidence-metric {{ padding: 5px 7px; }}
    .evidence-row {{ padding: 6px 0 3px; }}
    .evidence-row p {{ line-height: 1.4; }}
  }}
</style>
</head>
<body>
<section class="cover">
  <h1>{_h(packet['title'])}</h1>
  <p>Generated at: <strong>{_h(summary['generated_at'])}</strong></p>
  <p>Cases: <strong>{summary['case_count']}</strong> | Successful: <strong>{summary['success_count']}</strong> | Failed: <strong>{summary['failure_count']}</strong></p>
  <p class="notice">Research-use review packet. This output is informational and intended for expert evaluation, not direct medical advice.</p>
</section>
{''.join(body)}
</body>
</html>"""


def _packet_config(audience: str) -> dict[str, str]:
    configs = {
        "doctor": {
            "title": "INFERMed Doctor Mode Responses",
            "markdown_filename": "doctor_review.md",
            "html_filename": "doctor_review.html",
            "pdf_filename": "doctor_mode_response.pdf",
        },
        "patient": {
            "title": "INFERMed Patient Mode Responses",
            "markdown_filename": "patient_review.md",
            "html_filename": "patient_review.html",
            "pdf_filename": "patient_mode_response.pdf",
        },
        "pv_research": {
            "title": "INFERMed Pharmacovigilance and Research Mode Responses",
            "markdown_filename": "pharmacovigilance_research_review.md",
            "html_filename": "pharmacovigilance_research_review.html",
            "pdf_filename": "pharmacovigilance_research_mode_response.pdf",
        },
    }
    return configs.get(audience, configs["doctor"])


def _evidence_html(evidence: dict[str, Any]) -> str:
    cards = []
    for key, label in [
        ("overview", "Overview"),
        ("openfda", "OpenFDA"),
        ("internal", "Internal"),
        ("mechanisms", "Mechanisms"),
    ]:
        card = evidence.get(key) or {}
        metrics = []
        for metric in card.get("metrics") or []:
            metrics.append(
                "<div class='evidence-metric'>"
                f"<span>{_h(metric.get('label'))}</span><strong>{_h(metric.get('value'))}</strong>"
                "</div>"
            )
        rows = []
        for row in card.get("rows") or []:
            meta = f" <small>({_h(row.get('meta'))})</small>" if row.get("meta") else ""
            rows.append(
                "<div class='evidence-row'>"
                f"<h5>{_h(row.get('title') or 'Evidence item')}{meta}</h5>"
                f"<p>{_h(row.get('description') or 'No description returned.')}</p>"
                "</div>"
            )
        if not metrics and not rows:
            rows.append("<p class='evidence-empty'>No evidence rows returned.</p>")
        caveat = card.get("caveat")
        caveat_html = f"<p class='evidence-caveat'><strong>Caveat:</strong> {_h(caveat)}</p>" if caveat else ""
        cards.append(
            f"<section class='evidence-card'><h4>{_h(label)}</h4>"
            f"<div class='evidence-metrics'>{''.join(metrics)}</div>"
            f"<div class='evidence-rows'>{''.join(rows)}</div>{caveat_html}</section>"
        )

    references = evidence.get("references") or []
    if references:
        reference_rows = []
        for reference in references:
            meta = f" <small>({_h(reference.get('meta'))})</small>" if reference.get("meta") else ""
            reference_rows.append(
                "<div class='evidence-row'>"
                f"<h5>{_h(reference.get('title') or 'Reference')}{meta}</h5>"
                f"<p>{_h(reference.get('description') or 'No description returned.')}</p>"
                "</div>"
            )
        cards.append(
            "<section class='evidence-card evidence-references'><h4>References and caveats</h4>"
            f"<div class='evidence-rows'>{''.join(reference_rows)}</div></section>"
        )
    return f"<div class='evidence-grid'>{''.join(cards)}</div>"


def _try_render_pdf(html_path: Path, pdf_path: Path) -> dict[str, Any]:
    browser_pdf = _try_browser_pdf(html_path, pdf_path)
    if browser_pdf.get("ok"):
        return browser_pdf

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "reason": f"{browser_pdf.get('reason', 'Headless browser rendering failed')} Python Playwright is not installed.",
        }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.pdf(path=str(pdf_path), format="A4", print_background=True, margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
            browser.close()
        return {"ok": True, "path": str(pdf_path)}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _try_browser_pdf(html_path: Path, pdf_path: Path) -> dict[str, Any]:
    browser = _find_browser_executable()
    if not browser:
        return {"ok": False, "reason": "No headless Edge/Chrome executable found."}
    resolved_pdf_path = pdf_path.resolve()
    command = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={resolved_pdf_path}",
        html_path.resolve().as_uri(),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "reason": f"Headless browser PDF failed: {exc}"}
    if completed.returncode == 0 and pdf_path.exists():
        return {"ok": True, "path": str(pdf_path), "renderer": browser}
    return {
        "ok": False,
        "reason": f"Headless browser returned {completed.returncode}: {(completed.stderr or completed.stdout).strip()[:500]}",
    }


def _find_browser_executable() -> str:
    for name in ("chrome", "msedge", "chromium", "chrome.exe", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LocalAppData", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _get_json(url: str, timeout_s: int) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


def _parse_patient_context(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("--patient-context-json must be a JSON object.")
    return value


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return " ".join(text.split())


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe or "case"


def _h(value: Any) -> str:
    return html.escape(_clean(value))


def _markdown_html(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "<p>No text returned.</p>"
    return _MARKDOWN_RENDERER.render(text)


if __name__ == "__main__":
    raise SystemExit(main())
