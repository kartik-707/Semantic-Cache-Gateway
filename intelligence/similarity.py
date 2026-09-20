"""
intelligence/similarity.py

Given a query embedding, searches candidate CacheEntry objects and decides
exact / semantic / miss, producing a CacheDecision.

Design notes (why, not just how):
- search_similar() takes candidates as a plain argument rather than reaching
  into a real vector store. Per Phase 1's definition of done, this needs to
  work against a hardcoded CacheEntry list with no live Qdrant — wiring the
  real store is explicitly Phase 2's job (the sync point where Person A
  exposes POST /cache/store). Keeping the store swap-in isolated to one
  function (search_similar) is what makes that Phase 2 change a drop-in
  rather than a rewrite.
- Leakage filtering happens BEFORE ranking, not after. If we ranked first
  and filtered second, a leaking entry could still show up in intermediate
  results or logs before being discarded — filtering first means an entry
  that isn't allowed for this user never enters the candidate pool at all.
- decide() is deliberately separate from search_similar(). Search answers
  "what's the best-matching allowed entry and how similar is it?" Decide
  answers "given that score and this policy, what do we do?" Splitting them
  means Phase 2's judge integration only has to touch decide(), not the
  search logic.
"""

from typing import List, Optional
import time

from contracts.cache import CacheEntry
from contracts.decision import CacheDecision
from contracts.policy import PolicyConfig


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    # embeddings.py L2-normalizes vectors, so cosine similarity is a plain
    # dot product here — no need to re-derive norms on every comparison.
    return sum(x * y for x, y in zip(a, b))


def _is_allowed(entry: CacheEntry, user_id: Optional[str]) -> bool:
    """Cross-user leakage guard. A user-scoped entry (entry.user_id is not
    None) must never be returned to a different user — even at 1.0
    similarity. A shared entry (user_id is None) is allowed for anyone."""
    if entry.user_id is None:
        return True
    return entry.user_id == user_id


def search_similar(
    query_embedding: List[float],
    candidates: List[CacheEntry],
    feature_name: str,
    user_id: Optional[str] = None,
) -> tuple:
    """Filters candidates by feature + user-scope, then returns the single
    best match by cosine similarity, or None if nothing allowed remains.

    Returns (best_entry_or_None, leakage_filtered_count). The count exists
    so callers (decide(), and eventually near-miss logging) can distinguish
    "this scored low because nothing similar exists" from "this scored low
    because the only similar entry belonged to a different user and was
    correctly excluded" — those look identical from the score alone but
    mean very different things when someone's reading the logs later.

    Returns the CacheEntry itself (not just a score) — the caller needs the
    entry's response_payload to actually serve a semantic hit, not just
    know that one exists.
    """
    same_feature = [c for c in candidates if c.feature_name == feature_name]
    allowed = [c for c in same_feature if _is_allowed(c, user_id)]
    leakage_filtered_count = len(same_feature) - len(allowed)

    if not allowed:
        return None, leakage_filtered_count

    best = max(allowed, key=lambda c: _cosine_similarity(query_embedding, c.embedding))
    return best, leakage_filtered_count


def decide(
    query_embedding: List[float],
    candidates: List[CacheEntry],
    policy: PolicyConfig,
    user_id: Optional[str] = None,
    request_tags: Optional[List[str]] = None,
) -> CacheDecision:
    """Turns a search result + policy into a CacheDecision. This is what
    Track A's proxy calls in place of the current always-miss stub."""
    start = time.perf_counter()
    request_tags = request_tags or []

    # Tag-based override: some features must never be served from cache
    # regardless of similarity (e.g. live_news_summary's own current_events tag).
    if any(tag in policy.disable_for_tags for tag in request_tags):
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        return CacheDecision(
            hit_type="miss",
            decision_reason=f"request tag(s) {request_tags} disabled for this policy",
            policy_applied=policy.feature_name,
            cache_latency_ms=latency_ms,
        )

    if not policy.cache_enabled:
        latency_ms = round((time.perf_counter() - start) * 1000, 3)
        return CacheDecision(
            hit_type="miss",
            decision_reason="cache_enabled is False for this policy",
            policy_applied=policy.feature_name,
            cache_latency_ms=latency_ms,
        )

    best, leakage_filtered_count = search_similar(query_embedding, candidates, policy.feature_name, user_id)
    latency_ms = round((time.perf_counter() - start) * 1000, 3)
    leakage_note = (
        f" ({leakage_filtered_count} entr{'y' if leakage_filtered_count == 1 else 'ies'} "
        f"excluded by user-scope check)"
        if leakage_filtered_count > 0 else ""
    )

    if best is None:
        return CacheDecision(
            hit_type="miss",
            decision_reason=f"no allowed candidate entries for this feature/user scope{leakage_note}",
            policy_applied=policy.feature_name,
            cache_latency_ms=latency_ms,
        )

    score = _cosine_similarity(query_embedding, best.embedding)

    if score >= policy.similarity_threshold:
        # judge_required_above <= score < threshold is the borderline band —
        # AT the threshold we serve directly without a judge call, per the
        # Phase 0 contract's own comment ("serve cache if score >= this").
        return CacheDecision(
            hit_type="semantic",
            matched_entry_id=best.entry_id,
            similarity_score=score,
            decision_reason=f"score {score:.4f} >= threshold {policy.similarity_threshold}{leakage_note}",
            policy_applied=policy.feature_name,
            cache_latency_ms=latency_ms,
        )

    if score >= policy.judge_required_above:
        # This is the hook Phase 2's judge.py plugs into — decide() stops
        # here and hands back "judge needed", it doesn't call the judge itself.
        return CacheDecision(
            hit_type="miss",
            matched_entry_id=best.entry_id,
            similarity_score=score,
            decision_reason=(
                f"score {score:.4f} in borderline band "
                f"[{policy.judge_required_above}, {policy.similarity_threshold}) "
                f"— judge required (not yet wired, Phase 2){leakage_note}"
            ),
            policy_applied=policy.feature_name,
            judge_used=False,
            cache_latency_ms=latency_ms,
        )

    return CacheDecision(
        hit_type="miss",
        matched_entry_id=best.entry_id,
        similarity_score=score,
        decision_reason=f"score {score:.4f} below judge floor {policy.judge_required_above}{leakage_note}",
        policy_applied=policy.feature_name,
        cache_latency_ms=latency_ms,
    )


if __name__ == "__main__":
    from intelligence.embeddings import embed
    from intelligence.policy_loader import get_policy

    policy = get_policy("faq_bot")

    stub_entries = [
        CacheEntry(
            embedding=embed("What is your return policy?"),
            original_prompt_hash="hash1",
            normalized_prompt="what is your return policy?",
            model="test-model",
            temperature=0.7,
            feature_name="faq_bot",
            user_id=None,  # shared entry
            response_payload={"choices": [{"message": {"content": "30-day returns."}}]},
            ttl_seconds=3600,
        ),
        CacheEntry(
            embedding=embed("How do I reset my password?"),
            original_prompt_hash="hash2",
            normalized_prompt="how do i reset my password?",
            model="test-model",
            temperature=0.7,
            feature_name="faq_bot",
            user_id="user_42",  # user-scoped entry
            response_payload={"choices": [{"message": {"content": "Click 'forgot password'."}}]},
            ttl_seconds=3600,
        ),
    ]

    # Test 1: near-duplicate of the shared entry should be a semantic hit
    q1 = embed("what's your return policy??")
    d1 = decide(q1, stub_entries, policy, user_id=None)
    print("Test 1 (near-duplicate, shared entry):", d1.hit_type, d1.similarity_score)
    assert d1.hit_type == "semantic", "expected a semantic hit on a near-duplicate"

    # Test 2: leakage check — user_99 must NOT get user_42's password-reset entry,
    # even when asking the identical question. Reason string should now name
    # the leakage filter explicitly.
    q2 = embed("How do I reset my password?")
    d2 = decide(q2, stub_entries, policy, user_id="user_99")
    print("Test 2 (leakage attempt, wrong user):", d2.hit_type, "-", d2.decision_reason)
    assert d2.hit_type == "miss", "leakage check failed — wrong user got a hit"
    assert "user-scope check" in d2.decision_reason, "leakage reason should now be explicit"

    # Test 3: correct user CAN get their own scoped entry
    d3 = decide(q2, stub_entries, policy, user_id="user_42")
    print("Test 3 (correct user, scoped entry):", d3.hit_type, d3.similarity_score)
    assert d3.hit_type == "semantic", "correct user should get their own scoped entry"

    # Test 4: genuinely unrelated prompt should miss
    q4 = embed("What's the weather like in Tokyo?")
    d4 = decide(q4, stub_entries, policy, user_id=None)
    print("Test 4 (unrelated prompt):", d4.hit_type, d4.similarity_score)
    assert d4.hit_type == "miss", "unrelated prompt should not hit"

    # Test 5: tag override — current_events tag on a request against a
    # policy that disables it entirely
    news_policy = get_policy("live_news_summary")
    d5 = decide(q1, [], news_policy, user_id=None, request_tags=["current_events"])
    print("Test 5 (tag override):", d5.hit_type, d5.decision_reason)
    assert d5.hit_type == "miss" and "disabled" in d5.decision_reason

    print("\nAll assertions passed.")