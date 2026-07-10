"""Layer 5 — HTTP interface.

Pure adapter: parse request → call orchestrator → return JSON.
No business logic lives here.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, PlainTextResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from cca.proxy.cost_tracker import CostTracker
from cca.proxy.orchestrator import build_orchestrator
from cca.proxy.semantic_cache import SemanticCache

logger = logging.getLogger(__name__)

_DATA_DIR = Path.home() / ".cca" / "proxy"
_CACHE_PATH = _DATA_DIR / "semantic_cache.json"
_TRACKER_PATH = _DATA_DIR / "cost_tracker.json"


def _require_fastapi() -> None:
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI and httpx are required for the proxy server.\n"
            "Install: pip install 'token-slayer[proxy]'"
        )


def create_app(
    preferred_provider: str | None = None,
    cache_ttl: float = 3_600,
    similarity_threshold: float = 0.90,
    disable_cache: bool = False,
    disable_routing: bool = False,
) -> "FastAPI":
    _require_fastapi()

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Build cache stack (Layer 2) ───────────────────────────────────────────
    semantic = SemanticCache(
        similarity_threshold=similarity_threshold,
        ttl=cache_ttl,
        persist_path=None if disable_cache else _CACHE_PATH,
    )
    redis_url = os.environ.get("REDIS_URL")
    if redis_url and not disable_cache:
        try:
            from cca.proxy.redis_cache import build_cache_stack
            cache = build_cache_stack(semantic, redis_url=redis_url)
            logger.info("Two-layer cache: Redis L1 + SemanticCache L2")
        except ImportError:
            cache = semantic
            logger.warning("redis package not installed — using SemanticCache only")
    else:
        cache = semantic

    # ── Build orchestrator (Layer 4) ──────────────────────────────────────────
    tracker = CostTracker(persist_path=_TRACKER_PATH)
    orchestrator = build_orchestrator(
        cache=cache,
        tracker=tracker,
        preferred_provider=preferred_provider,
        disable_cache=disable_cache,
        disable_routing=disable_routing,
    )

    # ── FastAPI app ───────────────────────────────────────────────────────────
    app = FastAPI(
        title="Token Slayer Proxy",
        description="LLM cost-reduction middleware: semantic cache + smart routing + failover",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/v1/messages")
    async def proxy_messages(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            result = orchestrator.handle(
                messages=body.get("messages", []),
                system=body.get("system", ""),
                model_override=body.get("model"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        payload = result.response_data.copy()
        payload["_proxy"] = {
            "model_chosen": result.model_used,
            "complexity": result.complexity,
            "latency_s": result.latency_s,
            "actual_cost_usd": round(result.actual_cost_usd, 6),
            "baseline_cost_usd": round(result.baseline_cost_usd, 6),
            "savings_usd": round(result.baseline_cost_usd - result.actual_cost_usd, 6),
            "cache_hit": result.cache_hit,
        }
        return JSONResponse(payload)

    @app.get("/dashboard")
    async def dashboard(hours: float = 24.0) -> JSONResponse:
        return JSONResponse({
            "period_hours": hours,
            "cost_summary": tracker.summarize(since_hours=hours).to_dict(),
            "cache_stats": cache.stats(),
        })

    @app.get("/dashboard/text", response_class=PlainTextResponse)
    async def dashboard_text(hours: float = 24.0) -> str:
        return tracker.dashboard_text(since_hours=hours)

    @app.get("/health")
    async def health() -> JSONResponse:
        stats = cache.stats()
        entry_count = (
            stats.get("semantic", stats).get("entry_count", 0)
        )
        return JSONResponse({"status": "ok", "cache_entries": entry_count})

    @app.delete("/cache")
    async def clear_cache() -> JSONResponse:
        cache.clear()
        tracker.reset()
        return JSONResponse({"cleared": True, "cost_tracker_reset": True})

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080, **kwargs) -> None:
    _require_fastapi()
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required to run the server.\n"
            "Install: pip install 'token-slayer[proxy]'"
        ) from exc
    uvicorn.run(create_app(**kwargs), host=host, port=port, log_level="info")
