"""
intelligence/policy_loader.py

Reads a per-feature YAML policy file, validates it against the Phase 0
`PolicyConfig` contract, and hands back a typed object.

Design notes (why, not just how):
- Policies are cached in memory after first load, same reasoning as the
  embedding LRU cache in embeddings.py: re-reading + re-validating YAML on
  every request is wasted I/O for config that essentially never changes
  mid-run. If you edit a YAML file, restart the process to pick it up —
  that's an intentional tradeoff for a hot path, not an oversight.
- Failure is loud, not silent. A missing feature_name or a YAML file that
  doesn't match PolicyConfig raises immediately, with the feature_name and
  the list of what's actually on disk. Silently falling back to a default
  threshold would mean a caching bug that looks like correct behavior —
  exactly the kind of thing that's expensive to debug later. Better to
  break the request loudly than serve semantically wrong cached answers.
- get_policy() is the only function the proxy (or anything else) should
  call. Everything else here is a private implementation detail.
"""

from pathlib import Path
from typing import Dict
import yaml

from contracts.policy import PolicyConfig

_POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"
_policy_cache: Dict[str, PolicyConfig] = {}


def _available_features() -> list:
    """Lists feature_names that have a YAML file on disk — used to build
    a helpful error message when someone requests a policy that doesn't exist."""
    if not _POLICIES_DIR.exists():
        return []
    return sorted(p.stem for p in _POLICIES_DIR.glob("*.yaml"))


def _load_from_disk(feature_name: str) -> PolicyConfig:
    path = _POLICIES_DIR / f"{feature_name}.yaml"

    if not path.exists():
        available = _available_features()
        raise FileNotFoundError(
            f"No policy file for feature_name='{feature_name}' "
            f"(expected {path}). Available policies: {available}"
        )

    with open(path, "r") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Policy file {path} is empty or invalid YAML.")

    try:
        policy = PolicyConfig(**raw)
    except Exception as e:
        # Re-raise with the offending file named — a bare pydantic
        # ValidationError doesn't tell you *which* YAML file was bad
        # when you're loading several.
        raise ValueError(f"Policy file {path} failed PolicyConfig validation: {e}") from e

    if policy.feature_name != feature_name:
        # Catches a copy-paste mistake: e.g. live_news_summary.yaml whose
        # internal feature_name field still says "faq_bot".
        raise ValueError(
            f"Policy file {path} has feature_name='{policy.feature_name}', "
            f"but was loaded for '{feature_name}'. These must match."
        )

    return policy


def get_policy(feature_name: str) -> PolicyConfig:
    """Public entry point. Returns a validated PolicyConfig for the given
    feature, loading + caching it from policies/<feature_name>.yaml on
    first call."""
    if feature_name not in _policy_cache:
        _policy_cache[feature_name] = _load_from_disk(feature_name)
    return _policy_cache[feature_name]


def clear_cache() -> None:
    """Mainly for tests — forces the next get_policy() call to re-read
    from disk instead of returning a stale in-memory copy."""
    _policy_cache.clear()


if __name__ == "__main__":
    # Standalone sanity check — no proxy needed.
    for name in ["faq_bot", "code_explainer", "live_news_summary", "personal_account_assistant"]:
        p = get_policy(name)
        print(
            f"{name:28s} threshold={p.similarity_threshold}  "
            f"ttl={p.ttl_seconds}s  user_scoped={p.user_scoped}  "
            f"disable_for_tags={p.disable_for_tags}"
        )

    # Confirm caching actually avoids a second disk read
    import time
    start = time.perf_counter()
    get_policy("faq_bot")
    cached_call_us = (time.perf_counter() - start) * 1_000_000
    print(f"\ncached get_policy('faq_bot') call took {cached_call_us:.1f}µs (should be near-instant)")

    # Confirm the loud-failure path works
    try:
        get_policy("does_not_exist")
        print("ERROR: expected FileNotFoundError, none raised")
    except FileNotFoundError as e:
        print(f"\ncorrectly raised for unknown feature: {e}")