from pydantic import BaseModel
from typing import List

class PolicyConfig(BaseModel):
    feature_name: str
    similarity_threshold: float      
    ttl_seconds: int                   
    cache_enabled: bool = True
    tags: List[str] = []              
    user_scoped: bool = False           
    judge_required_above: float        
    disable_for_tags: List[str] = []   