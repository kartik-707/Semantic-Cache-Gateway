from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class NearMissLog(BaseModel):
    prompt_hash: str
    similarity_score: float
    threshold_at_time: float
    decision: Literal["miss"]          # always miss — that's what a near-miss is
    feature_name: str
    timestamp: datetime = None
    judge_rejected: bool = False       # True if judge downgraded a borderline hit

    def model_post_init(self, __context):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()