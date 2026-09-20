# Handoff Note — Phase 1 Track B

```
Author: Sahil (Track B)
Phase completed: Phase 1 Track B (embeddings, policy loader, similarity + decision engine)
```

---

## Done

- `intelligence/embeddings.py` — local `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim), normalizes prompts (lowercase + strip) before embedding, LRU-cached so repeat prompts aren't re-embedded. Public entry point: `embed(text) -> List[float]`.
- `intelligence/policy_loader.py` — reads `policies/<feature_name>.yaml`, validates against `PolicyConfig`, caches in memory after first load. Raises loudly (`FileNotFoundError` / `ValueError`) on a missing or malformed policy rather than silently falling back to defaults. Public entry point: `get_policy(feature_name) -> PolicyConfig`.
- `policies/` — added 3 new feature configs alongside your existing `faq_bot.yaml`: `code_explainer.yaml`, `live_news_summary.yaml`, `personal_account_assistant.yaml` — each with meaningfully different thresholds, TTLs, and tag rules (see the files for specifics; `personal_account_assistant` is `user_scoped: true`, `live_news_summary` disables caching entirely for its own `current_events` tag).
- `intelligence/similarity.py` — two public functions:
  - `search_similar(query_embedding, candidates, feature_name, user_id) -> (best_entry_or_None, leakage_filtered_count)` — filters candidates by feature + user-scope **before** ranking, so a leaking entry never competes for the top spot. Returns how many entries got excluded by the user-scope check, not just the survivor.
  - `decide(query_embedding, candidates, policy, user_id, request_tags) -> CacheDecision` — applies tag-based `disable_for_tags` overrides and `cache_enabled` first, then calls `search_similar`, then buckets the score into semantic hit / judge-required miss / plain miss per the policy's thresholds.
- All three tested against stub `CacheEntry` lists per Phase 1's definition of done — no live proxy, no real Qdrant needed. Test file is the `if __name__ == "__main__":` block at the bottom of each module; run with `python -m intelligence.<module>`.

## Half-done / in progress

- Nothing left half-done for Phase 1 scope. `judge.py` and `analysis.py` (mentioned in your original handoff's folder listing) are Phase 2 and Phase 3 work respectively — intentionally not started yet, so as not to build ahead of the Phase 2 sync point.

## Deviations from the Phase 0 contract

- None. All three files import directly from `contracts/` and validate cleanly against `CacheEntry`, `CacheDecision`, and `PolicyConfig` as committed.

## Known issues / things that will bite the next owner

- **`CacheDecision.decision_reason` now sometimes includes a leakage note**, e.g. `"... (1 entry excluded by user-scope check)"`, appended whenever a user-scoped entry gets filtered out during search — regardless of whether the final decision is a hit or a miss. This wasn't in the original Phase 0 discussion. Worth keeping in mind if any Phase 2/3 code (near-miss logging, threshold analysis) parses or pattern-matches on `decision_reason` strings — the format isn't fixed/enumerated, so exact-string matching against it would be fragile either way.
- Real-model similarity scores for genuine paraphrases came out lower than expected during testing — e.g. "What's your return policy?" vs "How do I send something back?" scored ~0.31 with `all-MiniLM-L6-v2`, well below `faq_bot`'s `0.92` threshold. Not a bug, just a heads-up that threshold tuning (Phase 3) will matter more than the plan doc's example numbers might suggest — the current YAML thresholds are placeholders, not calibrated values.
- `qdrant-client` is now in `requirements.txt` but unused — `similarity.py`'s `search_similar` takes candidates as a plain list argument by design, so wiring it to real Qdrant at the Phase 2 sync point should be a drop-in inside `search_similar` rather than a rewrite of the calling code.

## Where to start next

- Phase 2 sync point: wire `search_similar`'s candidate list to a live Qdrant query instead of a stub list, once Track A exposes `POST /cache/store` for seeding.
- Judge integration (`judge.py`) plugs into `decide()`'s borderline-band branch (`judge_required_above <= score < threshold`), which currently just returns a miss with `judge_used=False` and a note that the judge isn't wired yet.
