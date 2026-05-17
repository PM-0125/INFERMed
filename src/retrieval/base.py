from __future__ import annotations

from typing import Protocol

from src.core.evidence import EvidenceItem, SourceStatus


class RetrievalAdapter(Protocol):
    name: str

    def status(self) -> SourceStatus: ...

    def retrieve_drug(self, drug_name: str) -> list[EvidenceItem]: ...

    def retrieve_pair(self, drug_a: str, drug_b: str) -> list[EvidenceItem]: ...
