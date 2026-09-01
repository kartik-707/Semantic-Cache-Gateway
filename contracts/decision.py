from pydantic import BaseModel
from typing import Optional, Literal

class CacheDecision(BaseModel):
    hit_type: Literal["exact", "semantic", "miss"]
    matched_entry_id: Optional[str] = None
    similarity_score: Optional[float] = None
    decision_reason: str               # human-readable, e.g. "below threshold"
    policy_applied: str                # feature_name of the policy used
    judge_used: bool = False
    judge_verdict: Optional[Literal["YES", "NO"]] = None
    cache_latency_ms: float