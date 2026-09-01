from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

class CacheEntry(BaseModel):
    entry_id: str = None
    embedding: List[float]             # the vector
    original_prompt_hash: str          # sha256 of raw user message
    normalized_prompt: str             # lowercased, stripped
    system_prompt_hash: Optional[str] = None
    model: str
    temperature: float
    feature_name: str
    user_id: Optional[str] = None      # null = shared cache entry
    response_payload: dict             # full provider response
    similarity_score_at_store_time: Optional[float] = None
    ttl_seconds: int
    created_at: datetime = None
    tags: List[str] = []

    def model_post_init(self, __context):
        if self.entry_id is None:
            self.entry_id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = datetime.utcnow()