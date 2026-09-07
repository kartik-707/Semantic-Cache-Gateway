import os
import json
import hashlib
import redis.asyncio as aioredis
from dotenv import load_dotenv
from contracts.proxy import ProxyRequest

load_dotenv()

redis_client = aioredis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=True,
)

def make_exact_key(request: ProxyRequest) -> str:
    raw = f"{request.model}:{request.feature_name}:{request.messages[-1].content.strip().lower()}"
    return "exact:" + hashlib.sha256(raw.encode()).hexdigest()

async def get_exact(request: ProxyRequest):
    key = make_exact_key(request)
    cached = await redis_client.get(key)
    if cached:
        return json.loads(cached)
    return None

async def set_exact(request: ProxyRequest, response: dict, ttl: int):
    key = make_exact_key(request)
    await redis_client.setex(key, ttl, json.dumps(response))    