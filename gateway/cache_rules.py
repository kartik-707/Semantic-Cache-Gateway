import hashlib
import json
from dataclasses import dataclass

from contracts.policy import PolicyConfig
from contracts.proxy import ProxyRequest


@dataclass(frozen=True)
class CacheContext:
    enabled: bool
    reason: str
    exact_key: str
    compatibility_key: str
    ttl_seconds: int
    tags: tuple[str, ...]


def _digest(value: object) -> str:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_policy(policy: PolicyConfig) -> None:
    if not (
        0 <= policy.judge_required_above
        <= policy.similarity_threshold
        <= 1
    ):
        raise ValueError("Policy thresholds must satisfy 0 <= floor <= threshold <= 1")

    if policy.ttl_seconds <= 0:
        raise ValueError("Policy TTL must be positive")


def build_context(
    request: ProxyRequest,
    policy: PolicyConfig,
) -> CacheContext:
    validate_policy(policy)

    tags = tuple(sorted(set(policy.tags) | set(request.tags)))

    # A request can shorten the policy TTL, but cannot extend it.
    ttl = min(
        policy.ttl_seconds,
        request.cache_ttl_override or policy.ttl_seconds,
    )

    messages = [message.model_dump() for message in request.messages]

    compatibility = {
        "version": 2,
        "model": request.model,
        "feature_name": request.feature_name,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "tags": tags,
        "policy": policy.model_dump(mode="json"),
        "effective_ttl": ttl,
        "history": messages[:-1],
        "last_role": messages[-1]["role"],
    }

    compatibility_key = _digest(compatibility)

    # Preserve case and whitespace for exact caching.
    exact_key = "exact:v2:" + _digest({
        "compatibility_key": compatibility_key,
        "messages": messages,
    })

    enabled = True
    reason = "cache eligible"

    if request.cache_disabled:
        enabled = False
        reason = "cache disabled by request"

    elif not policy.cache_enabled:
        enabled = False
        reason = "cache disabled by feature policy"

    elif set(tags) & set(policy.disable_for_tags):
        enabled = False
        reason = "cache disabled by policy tag rule"

    elif (
        policy.user_scoped or "user_specific" in tags
    ) and request.user_id is None:
        enabled = False
        reason = "private request has no user_id; cache bypassed"

    elif request.messages[-1].role != "user":
        enabled = False
        reason = "final message is not a user message; cache bypassed"

    elif not request.messages[-1].content.strip():
        enabled = False
        reason = "final user message is blank; cache bypassed"

    return CacheContext(
        enabled=enabled,
        reason=reason,
        exact_key=exact_key,
        compatibility_key=compatibility_key,
        ttl_seconds=ttl,
        tags=tags,
    )