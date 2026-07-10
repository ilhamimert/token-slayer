"""LLM cost-reduction middleware: semantic cache, smart routing, failover."""
from __future__ import annotations

from cca.proxy.cost_tracker import CostTracker
from cca.proxy.orchestrator import OrchestratorResult, ProxyOrchestrator, build_orchestrator
from cca.proxy.prompt_chunker import PromptChunker
from cca.proxy.router import Complexity, ModelConfig, classify_complexity, get_model_for_complexity
from cca.proxy.semantic_cache import SemanticCache

__all__ = [
    "ModelConfig",
    "Complexity",
    "classify_complexity",
    "get_model_for_complexity",
    "SemanticCache",
    "CostTracker",
    "PromptChunker",
    "ProxyOrchestrator",
    "OrchestratorResult",
    "build_orchestrator",
]
