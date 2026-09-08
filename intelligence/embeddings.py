"""
intelligence/embeddings.py

Turns a raw prompt string into a vector for similarity search.

Design notes (why, not just how):
- Normalization happens here, once, so every downstream consumer (similarity
  search, cache write path) works off the same normalized string. If we
  normalized in multiple places we'd risk drift between what gets embedded
  at store-time vs query-time, which would silently degrade match quality.
- The LRU cache is keyed on the *normalized* string, not the raw one, so
  "What's your return policy?" and "  what's your return policy?  " share
  a cache entry instead of missing on whitespace/case differences.
- Local model (sentence-transformers) instead of an API call: no key, no
  rate limit, no network dependency for something that runs on every
  single request. This is a latency-sensitive hot path.
"""

from functools import lru_cache
from typing import List

_MODEL_NAME = "all-MiniLM-L6-v2"  # 384-dim, fast, good enough for FAQ-style semantic match
_model = None  # lazy-loaded so importing this module doesn't eat startup time


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def normalize_prompt(text: str) -> str:
    """Lowercase + strip whitespace. Must match whatever Track A uses for
    its exact-cache hash, or the two caches will disagree about what
    counts as 'the same prompt'."""
    return text.strip().lower()


@lru_cache(maxsize=2048)
def _embed_normalized(normalized_text: str) -> tuple:
    # Returns a tuple (not a list) because lru_cache requires hashable
    # return values to be safely reused — caller-facing embed() converts
    # back to a list to match the CacheEntry.embedding: List[float] contract.
    vector = _get_model().encode(normalized_text, normalize_embeddings=True)
    return tuple(float(x) for x in vector)


def embed(text: str) -> List[float]:
    """Public entry point. Give it a raw prompt, get back a vector.

    Embeddings are L2-normalized (normalize_embeddings=True) so that cosine
    similarity reduces to a plain dot product downstream in similarity.py —
    cheaper to compute and avoids re-deriving the norm on every comparison.
    """
    normalized = normalize_prompt(text)
    return list(_embed_normalized(normalized))


def cache_info():
    """Exposes hit/miss stats on the repeat-embedding cache — useful when
    tuning workloads in Phase 3, and handy for sanity-checking in tests."""
    return _embed_normalized.cache_info()


if __name__ == "__main__":
    # Quick standalone sanity check — no proxy, no store, just the module.
    a = embed("What's your return policy?")
    b = embed("How do I send something back?")
    c = embed("  WHAT'S YOUR RETURN POLICY?  ")

    def cosine(u, v):
        return sum(x * y for x, y in zip(u, v))  # already normalized -> dot product

    print(f"dims: {len(a)}")
    print(f"return-policy vs send-something-back similarity: {cosine(a, b):.4f}")
    print(f"return-policy vs itself (diff casing/whitespace) similarity: {cosine(a, c):.4f}")
    print(f"cache info after 3 calls (2 unique strings): {cache_info()}")