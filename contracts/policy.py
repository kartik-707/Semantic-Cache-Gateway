from pydantic import BaseModel
from typing import List

class PolicyConfig(BaseModel):
    feature_name: str
    similarity_threshold: float        # e.g. 0.92 — serve cache if score >= this
    ttl_seconds: int                   # how long entries live
    cache_enabled: bool = True
    tags: List[str] = []               # tags this feature may produce
    user_scoped: bool = False          # if True, user_id must match on lookup
    judge_required_above: float        # e.g. 0.85 — judge if score in [this, threshold)
    disable_for_tags: List[str] = []   # e.g. ["current_events"] → skip cache entirely