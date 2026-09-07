import uuid
import time
from fastapi import FastAPI
from contracts.proxy import ProxyRequest, ProxyResponse
from contracts.decision import CacheDecision
from gateway.adapter import call_provider
from gateway.cache_store import get_exact, set_exact

app = FastAPI(title="Semantic Cache Gateway")

DEFAULT_TTL = 3600  # 1 hour fallback if no policy TTL

@app.post("/v1/chat/completions", response_model=ProxyResponse)
async def chat_completions(request: ProxyRequest):
    start = time.time()

    # Step 1 — check exact cache
    cached = await get_exact(request)
    cache_latency = round((time.time() - start) * 1000, 2)

    if cached:
        decision = CacheDecision(
            hit_type="exact",
            matched_entry_id=cached.get("id"),
            decision_reason="exact prompt hash match in Redis",
            policy_applied=request.feature_name,
            cache_latency_ms=cache_latency,
        )
        return ProxyResponse(
            id=cached.get("id", str(uuid.uuid4())),
            model=cached.get("model", request.model),
            choices=cached.get("choices", []),
            usage=cached.get("usage"),
            cache_meta=decision,
        )

    # Step 2 — semantic check (stubbed for now, always miss)
    decision = CacheDecision(
        hit_type="miss",
        decision_reason="no exact match, semantic search not yet integrated",
        policy_applied=request.feature_name,
        cache_latency_ms=cache_latency,
    )

    # Step 3 — call provider
    response = await call_provider(request)

    # Step 4 — store in exact cache
    await set_exact(request, response, DEFAULT_TTL)

    return ProxyResponse(
        id=response.get("id", str(uuid.uuid4())),
        model=response.get("model", request.model),
        choices=response.get("choices", []),
        usage=response.get("usage"),
        cache_meta=decision,
    )