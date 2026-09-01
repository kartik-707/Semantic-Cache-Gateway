from pydantic import BaseModel
from typing import Optional, List

class Message(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str

class ProxyRequest(BaseModel):
    model: str
    messages: List[Message]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    feature_name: str          # which policy to apply, e.g. "faq_bot"
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    cache_disabled: bool = False
    cache_ttl_override: Optional[int] = None  # seconds, overrides policy TTL

class ProxyResponse(BaseModel):
    id: str
    model: str
    choices: List[dict]        # mirrors OpenAI response shape
    usage: Optional[dict] = None
    cache_meta: "CacheDecision"  # always attached

from .decision import CacheDecision  # noqa: E402
ProxyResponse.model_rebuild()