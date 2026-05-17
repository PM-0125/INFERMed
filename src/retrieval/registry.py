from __future__ import annotations

from src.config.data_policy import get_source_status
from src.config.settings import Settings, get_settings
from src.core.evidence import EvidenceItem, SourceStatus


class QLeverAdapterDisabled:
    name = "QLever RDF"

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            enabled=False,
            available=False,
            reason="Disabled for NVIDIA demo runtime",
        )

    def retrieve_drug(self, drug_name: str) -> list[EvidenceItem]:
        return []

    def retrieve_pair(self, drug_a: str, drug_b: str) -> list[EvidenceItem]:
        return []


def get_retrieval_source_status(settings: Settings | None = None) -> list[SourceStatus]:
    return get_source_status(settings or get_settings())
