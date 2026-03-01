from __future__ import annotations

from typing import Protocol

from receipt_shared.ai.registry import ModelDefinition
from receipt_shared.ai.types import AIRequest, ProviderResult, TokenUsage


class AIProvider(Protocol):
    def estimate_usage(self, request: AIRequest, model: ModelDefinition) -> TokenUsage:
        ...

    def generate(self, request: AIRequest, model: ModelDefinition) -> ProviderResult:
        ...
