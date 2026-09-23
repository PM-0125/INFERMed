"""Exact-identifier enrichment; never infer a drug identity from a search hit."""

import asyncio
import re
from copy import deepcopy
from dataclasses import replace

import httpx

from src.application.evidence_enrichment import EnrichmentBudget, enrich_profile_gaps
from src.application.events import stable_hash, utc_now
from src.application.interaction_modeling import build_drug_profile_graph
from src.application.source_tools import SourceToolInput, SourceToolResult
from src.domain.evidence.entities import EvidenceCard
from src.infrastructure.tools.registry import default_tool_registry
from src.infrastructure.tools.executor import CachedToolExecutor
from src.infrastructure.persistence.cache_store import SQLiteToolCacheStore


async def _fetch_json(url, params, timeout):
    async def fetch():
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise ValueError("Source response exceeded size limit")
                import json

                return json.loads(data)

    return await asyncio.wait_for(fetch(), timeout=timeout)


class ExactGraphTool:
    def __init__(self, name, source):
        self.name, self.source_name, self.version = name, source, "graph-1"

    def execute(self, request):
        identifier = request.parameters["identifier"]
        if self.source_name == "ChEMBL":
            if not re.fullmatch(r"CHEMBL\d+", identifier):
                raise ValueError("Invalid ChEMBL identifier")
            url = "https://www.ebi.ac.uk/chembl/api/data/mechanism.json"
            params = {"molecule_chembl_id": identifier, "limit": 100}
        else:
            if not re.fullmatch(
                r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})",
                identifier,
            ):
                raise ValueError("Invalid UniProt identifier")
            url = f"https://rest.uniprot.org/uniprotkb/{identifier}.json"
            params = {}
        data = asyncio.run(
            _fetch_json(url, params, float(request.parameters["timeout_s"]))
        )
        if not isinstance(data, dict):
            raise ValueError("Malformed source response")
        terms = []
        if self.source_name == "ChEMBL":
            for row in data.get("mechanisms", []):
                if row.get("molecule_chembl_id") != identifier:
                    continue
                target = row.get("target_chembl_id")
                if isinstance(target, str) and re.fullmatch(r"CHEMBL\d+", target):
                    terms.append(
                        (
                            "target",
                            target,
                            row.get("mechanism_of_action") or "Target association",
                        )
                    )
        elif data.get("primaryAccession") == identifier:
            for comment in data.get("comments", []):
                if comment.get("commentType") == "PATHWAY":
                    for item in comment.get("texts", []):
                        value = item.get("value")
                        if isinstance(value, str) and value.strip():
                            terms.append(
                                (
                                    "pathway",
                                    value.strip(),
                                    "Protein pathway annotation; not direct clinical DDI evidence",
                                )
                            )
        cards = []
        for kind, label, detail in terms:
            statement = f"{identifier}: {label}. {detail}"
            cards.append(
                EvidenceCard(
                    "ev_"
                    + stable_hash(
                        [
                            self.source_name,
                            identifier,
                            kind,
                            label,
                            request.normalized_concept_ids,
                        ]
                    )[:16],
                    "source-cache",
                    self.source_name,
                    "api",
                    "mechanism_enrichment",
                    statement,
                    "mechanistic",
                    request.normalized_concept_ids,
                    provenance={
                        "url": url,
                        "retrieved_at": utc_now(),
                        "identifier": identifier,
                    },
                    payload={"profile_kind": kind, "profile_label": label},
                    limitations=[
                        "Mechanistic annotation, not proof of a clinical interaction."
                    ],
                )
            )
        return SourceToolResult(
            self.name,
            self.source_name,
            True,
            evidence_cards=cards,
            normalized_payload={
                "identifier": identifier,
                "truncated": bool(data.get("page_meta", {}).get("next")),
            },
            raw_payload_hash=stable_hash(data),
        )


class CachedBoundedTool:
    def __init__(self, tool, executor, refresh):
        self.tool, self.executor, self.refresh = tool, executor, refresh

    def execute(self, request):
        # Deadline is operational, not part of semantic identity/cache keys.
        timeout = request.parameters["timeout_s"]
        semantic = replace(
            request,
            parameters={
                k: v for k, v in request.parameters.items() if k != "timeout_s"
            },
        )
        underlying = self.tool

        class Bound:
            def execute(self, tool_input):
                return underlying.execute(
                    replace(
                        tool_input,
                        parameters={**tool_input.parameters, "timeout_s": timeout},
                    )
                )

        return self.executor.execute(Bound(), semantic, refresh=self.refresh)


def enrich_pair_results(
    pair_results, *, analysis_id, settings, refresh=False, progress=None
):
    from src.application.use_cases.analyze_medication_set import decision_from_context

    allowed = {
        t.name for t in default_tool_registry().allowed_tools(settings.data_mode)
    }
    tools = {}
    requests = []
    skipped = []
    sources = (
        ("fetch_chembl_bioactivity", "ChEMBL", settings.enable_chembl),
        ("fetch_uniprot_targets", "UniProt", settings.enable_uniprot),
    )
    for name, source, enabled in sources:
        if enabled and name in allowed:
            tools[name] = ExactGraphTool(name, source)
    for result in pair_results:
        graph = build_drug_profile_graph(
            analysis_id=analysis_id,
            drugs=list(result.pair),
            context=result.context,
            evidence_cards=result.evidence_cards or [],
        )
        for side, drug in zip(("a", "b"), result.pair):
            gaps = graph.missing_elements.get(drug, [])
            info = (result.context.get("drugs") or {}).get(side) or {}
            molecule = info.get("chembl_id") or (info.get("ids") or {}).get("chembl_id")
            if (
                "targets" in gaps
                and isinstance(molecule, str)
                and re.fullmatch(r"CHEMBL\d+", molecule)
            ):
                requests.append(
                    SourceToolInput(
                        "fetch_chembl_bioactivity",
                        "ChEMBL",
                        "graph_mechanism",
                        [drug],
                        {"identifier": molecule},
                        "graph-1",
                    )
                )
            if "pathways" in gaps:
                for node in graph.nodes_for_drug(drug):
                    if node.type in {
                        "target",
                        "enzyme",
                        "transporter",
                    } and re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9]", node.label):
                        requests.append(
                            SourceToolInput(
                                "fetch_uniprot_targets",
                                "UniProt",
                                "graph_pathway",
                                [drug],
                                {"identifier": node.label},
                                "graph-1",
                            )
                        )
            if gaps and not any(drug in r.normalized_concept_ids for r in requests):
                skipped.append(
                    {
                        "drug": drug,
                        "reason": "no_actionable_exact_identifier",
                        "gaps": gaps,
                    }
                )
    executor = (
        CachedToolExecutor(SQLiteToolCacheStore(settings.sqlite_cache_path))
        if requests
        else None
    )
    bounded = {
        name: CachedBoundedTool(tool, executor, refresh) for name, tool in tools.items()
    }
    outcome = enrich_profile_gaps(
        requests=requests,
        available_tools=bounded,
        budget=EnrichmentBudget(
            max_calls=settings.enrichment_max_calls,
            deadline_s=settings.enrichment_deadline_s,
            source_timeout_s=settings.enrichment_source_timeout_s,
        ),
        progress=progress,
    )
    outcome.skipped.extend(skipped)
    updated = []
    for result in pair_results:
        context = deepcopy(result.context)
        cards = {c.evidence_id: c for c in result.evidence_cards or []}
        for source in outcome.results:
            if source.ok:
                for card in source.evidence_cards:
                    if set(card.drug_scope).issubset(set(result.pair)):
                        cards[card.evidence_id] = replace(card, analysis_id=analysis_id)
        context["graph_enrichment"] = {
            "skipped": outcome.skipped,
            "exhausted": outcome.exhausted,
            "sources": [
                {
                    "name": r.source_name,
                    "ok": r.ok,
                    "error": r.error,
                    "from_cache": r.from_cache,
                    "evidence_count": len(r.evidence_cards),
                }
                for r in outcome.results
            ],
        }
        updated.append(
            replace(
                result,
                context=context,
                evidence_cards=list(cards.values()),
                decision=decision_from_context(
                    analysis_id, context, list(cards.values()), list(result.pair)
                ),
            )
        )
    return updated
