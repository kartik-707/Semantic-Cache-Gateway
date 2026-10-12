import hashlib
import logging
import os
import secrets
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from openai import AsyncOpenAI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from starlette.concurrency import run_in_threadpool

from contracts.cache import CacheEntry
from contracts.decision import CacheDecision
from contracts.policy import PolicyConfig
from contracts.proxy import ProxyRequest, ProxyResponse
from gateway.adapter import call_provider
from gateway.cache_rules import (
    CacheContext,
    build_context,
    validate_policy,
)
from gateway.cache_store import get_exact, set_exact
from gateway.metrics import (
    CACHE_BYPASSES,
    CACHE_ERRORS,
    CACHE_LOOKUP_SECONDS,
    CACHE_RESPONSES,
    PROVIDER_CALLS,
    PROVIDER_ERRORS,
    PROVIDER_SECONDS,
    REQUEST_SECONDS,
)
from gateway.vector_store import VectorStore, normalize_vector
from intelligence.embeddings import embed
from intelligence.policy_loader import get_policy
from intelligence.similarity import decide


load_dotenv()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        redis_client = Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        stack.push_async_callback(redis_client.aclose)

        vectors = VectorStore(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            collection=os.getenv(
                "QDRANT_COLLECTION",
                "semantic_cache_phase2",
            ),
        )
        stack.push_async_callback(vectors.close)

        provider = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            timeout=60,
            max_retries=1,
        )
        stack.push_async_callback(provider.close)

        # Startup requires configured infrastructure to be reachable.
        await redis_client.ping()
        await vectors.initialize()

        app.state.redis = redis_client
        app.state.vectors = vectors
        app.state.provider = provider

        yield


app = FastAPI(
    title="Semantic Cache Gateway",
    lifespan=lifespan,
)


def load_policy(feature_name: str) -> PolicyConfig:
    try:
        policy = get_policy(feature_name)
        validate_policy(policy)
        return policy
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Unknown feature_name",
        ) from exc
    except Exception as exc:
        logger.error(
            "Policy loading failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="Feature policy is unavailable or invalid",
        ) from exc


def cache_failure(operation: str, exc: Exception) -> None:
    CACHE_ERRORS.labels(operation=operation).inc()
    # Avoid logging request text or exception messages containing payloads.
    logger.warning(
        "Cache operation %s failed: %s",
        operation,
        type(exc).__name__,
    )


def make_entry(
    body: ProxyRequest,
    context: CacheContext,
    embedding: list[float],
    response_payload: dict,
) -> CacheEntry:
    prompt = body.messages[-1].content

    system_messages = [
        message.model_dump()
        for message in body.messages
        if message.role in {"system", "developer"}
    ]

    # Compatibility keys already contain complete conversation context.
    # This separate hash is retained for future inspection/invalidation.
    import json

    system_hash = hashlib.sha256(
        json.dumps(
            system_messages,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    return CacheEntry(
        embedding=embedding,
        original_prompt_hash=hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest(),
        normalized_prompt=prompt.strip().lower(),
        system_prompt_hash=system_hash,
        model=body.model,
        temperature=body.temperature,
        feature_name=body.feature_name,
        user_id=body.user_id,
        response_payload=response_payload,
        ttl_seconds=context.ttl_seconds,
        created_at=datetime.now(timezone.utc),
        tags=list(context.tags),
    )


async def store_entry(
    request: Request,
    context: CacheContext,
    entry: CacheEntry,
) -> dict[str, bool]:
    stored = {"redis": False, "qdrant": False}

    try:
        await set_exact(request.app.state.redis, context, entry)
        stored["redis"] = True
    except Exception as exc:
        cache_failure("redis_write", exc)

    try:
        await request.app.state.vectors.store(context, entry)
        stored["qdrant"] = True
    except Exception as exc:
        cache_failure("qdrant_write", exc)

    return stored


def format_response(
    body: ProxyRequest,
    payload: dict,
    decision: CacheDecision,
) -> ProxyResponse:
    response = ProxyResponse(
        id=payload.get("id") or str(uuid.uuid4()),
        model=payload.get("model") or body.model,
        choices=payload.get("choices", []),
        usage=payload.get("usage"),
        cache_meta=decision,
    )

    CACHE_RESPONSES.labels(hit_type=decision.hit_type).inc()
    return response


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


@app.post("/v1/chat/completions", response_model=ProxyResponse)
async def chat_completions(body: ProxyRequest, request: Request):
    started = time.perf_counter()

    try:
        policy = load_policy(body.feature_name)
        context = build_context(body, policy)

        decision = CacheDecision(
            hit_type="miss",
            decision_reason=context.reason,
            policy_applied=body.feature_name,
            cache_latency_ms=0,
        )

        embedding = None
        cached_entry = None

        if not context.enabled:
            CACHE_BYPASSES.inc()

        else:
            # Exact lookup also obeys all feature and identity rules.
            try:
                cached_entry = await get_exact(
                    request.app.state.redis,
                    context,
                )
            except Exception as exc:
                cache_failure("redis_read", exc)

            if cached_entry is not None:
                decision = CacheDecision(
                    hit_type="exact",
                    matched_entry_id=cached_entry.entry_id,
                    decision_reason="compatible exact request found in Redis",
                    policy_applied=body.feature_name,
                    cache_latency_ms=0,
                )

            else:
                try:
                    # Sentence-transformers is synchronous CPU work.
                    embedding = normalize_vector(
                        await run_in_threadpool(
                            embed,
                            body.messages[-1].content,
                        )
                    )
                except Exception as exc:
                    cache_failure("embedding", exc)
                    decision.decision_reason = (
                        "embedding unavailable; provider fallback"
                    )

                if embedding is not None:
                    try:
                        candidates = await request.app.state.vectors.search(
                            context,
                            embedding,
                        )

                        decision = decide(
                            query_embedding=embedding,
                            candidates=candidates,
                            policy=policy,
                            user_id=body.user_id,
                            request_tags=list(context.tags),
                        )

                        if decision.hit_type == "semantic":
                            cached_entry = next(
                                (
                                    entry
                                    for entry in candidates
                                    if entry.entry_id
                                    == decision.matched_entry_id
                                ),
                                None,
                            )

                            if cached_entry is None:
                                decision = CacheDecision(
                                    hit_type="miss",
                                    decision_reason=(
                                        "semantic decision had no matching payload"
                                    ),
                                    policy_applied=body.feature_name,
                                    cache_latency_ms=0,
                                )

                        # Borderline decisions remain misses until
                        # Sahil integrates his judge.

                    except Exception as exc:
                        cache_failure("semantic_lookup", exc)
                        cached_entry = None
                        decision = CacheDecision(
                            hit_type="miss",
                            decision_reason=(
                                "semantic lookup unavailable; provider fallback"
                            ),
                            policy_applied=body.feature_name,
                            cache_latency_ms=0,
                        )

        lookup_seconds = time.perf_counter() - started
        decision.cache_latency_ms = round(lookup_seconds * 1000, 3)
        CACHE_LOOKUP_SECONDS.observe(lookup_seconds)

        if cached_entry is not None:
            return format_response(
                body,
                cached_entry.response_payload,
                decision,
            )

        PROVIDER_CALLS.inc()
        provider_started = time.perf_counter()

        try:
            payload = await call_provider(
                body,
                request.app.state.provider,
            )
        except Exception as exc:
            PROVIDER_ERRORS.inc()
            logger.warning(
                "Provider call failed: %s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="Upstream provider request failed",
            ) from exc
        finally:
            PROVIDER_SECONDS.observe(
                time.perf_counter() - provider_started
            )

        # If embedding failed, return the provider response without
        # storing an incomplete semantic entry.
        if context.enabled and embedding is not None:
            entry = make_entry(body, context, embedding, payload)
            await store_entry(request, context, entry)

        return format_response(body, payload, decision)

    finally:
        REQUEST_SECONDS.observe(time.perf_counter() - started)


class SeedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    choices: list[dict] = Field(min_length=1)
    usage: dict | None = None


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: ProxyRequest
    response: SeedResponse


@app.post("/cache/store")
async def seed_cache(
    body: SeedRequest,
    request: Request,
    x_cache_admin_token: str | None = Header(default=None),
):
    expected = os.getenv("CACHE_ADMIN_TOKEN", "")

    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Cache seeding is not configured",
        )

    if not secrets.compare_digest(
        (x_cache_admin_token or "").encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid cache admin token",
        )

    policy = load_policy(body.request.feature_name)
    context = build_context(body.request, policy)

    if not context.enabled:
        raise HTTPException(
            status_code=400,
            detail=context.reason,
        )

    if body.response.model != body.request.model:
        raise HTTPException(
            status_code=422,
            detail="Seed request and response model must match",
        )

    try:
        embedding = normalize_vector(
            await run_in_threadpool(
                embed,
                body.request.messages[-1].content,
            )
        )
    except Exception as exc:
        cache_failure("seed_embedding", exc)
        raise HTTPException(
            status_code=503,
            detail="Embedding model is unavailable",
        ) from exc

    entry = make_entry(
        body.request,
        context,
        embedding,
        body.response.model_dump(mode="json"),
    )

    stored = await store_entry(request, context, entry)

    if not all(stored.values()):
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Cache seed was only partially stored",
                "stored": stored,
                "entry_id": entry.entry_id,
            },
        )

    return {
        "entry_id": entry.entry_id,
        "stored": stored,
        "ttl_seconds": context.ttl_seconds,
    }