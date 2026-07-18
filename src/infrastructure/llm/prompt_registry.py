from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplateRef:
    name: str
    path: str
    version: str = "1"


class PromptRegistry:
    def __init__(self, templates: list[PromptTemplateRef] | None = None):
        self._templates = {item.name: item for item in (templates or [])}

    def register(self, template: PromptTemplateRef) -> None:
        self._templates[template.name] = template

    def get(self, name: str) -> PromptTemplateRef:
        return self._templates[name]
