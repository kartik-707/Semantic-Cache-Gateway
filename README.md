# Semantic Cache Gateway

> A policy-governed proxy that sits between your app and any LLM provider — detecting semantically similar requests, serving cached responses when safe, and tracking cost and latency savings in real time.

---

## What this is

Most LLM caching stops at exact string matching. That misses the point: natural language is paraphrastic by nature, and users asking the same question rarely type the same words twice.

This gateway uses vector embeddings to detect *intent similarity*, not string equality. A cached response for *"What's your return policy?"* is served for *"How do I send something back?"* — if, and only if, the policy for that feature says it's safe to do so, the similarity score clears the threshold, and (for borderline hits) a lightweight judge confirms the cached answer actually addresses the new prompt.

The result: meaningful cost and latency reduction without correctness or privacy tradeoffs.

---

## Architecture

```
Client Request
      │
      ▼
┌─────────────────────────────────┐
│         FastAPI Proxy           │  ← mirrors OpenAI /v1/chat/completions
│  - request normalization        │
│  - feature policy lookup        │
│  - cache_meta attached to resp  │
└────────────┬────────────────────┘
             │
      ┌──────┴──────┐
      │             │
      ▼             ▼
 Exact Cache    Embedding Engine
 (Redis hash)   (text-embedding-3-small)
                      │
                      ▼
               Vector Store (Qdrant)
               similarity search
                      │
              ┌───────┴────────┐
              │                │
           HIT (≥θ)      Borderline hit
              │           [θ_low, θ)
              │                │
              │           LLM Judge
              │           YES / NO
              │                │
         Serve cache      ┌────┴────┐
                          │        │
                        YES: NO: miss
                        serve  → LLM Provider
                               → store entry
                               → NearMissLog
```

---

## Key capabilities

**Semantic similarity matching** — embeddings over exact string match, so paraphrases hit the cache.

**Policy engine** — per-feature YAML config with independent similarity thresholds, TTLs, user scoping, and tag-based overrides. One threshold does not fit all use cases.

**Cross-user leakage prevention** — user-scoped cache entries are never served to a different user_id, even at 1.0 similarity.

**Borderline-hit judge** — when a similarity score falls in the configurable band below the threshold, a lightweight LLM validates whether the cached answer actually addresses the new prompt. Judge NO downgrades to a miss.

**Near-miss logging and threshold tuning** — misses above a low-water mark are logged to Postgres with their similarity scores. A threshold analysis module clusters these by feature and generates per-feature policy recommendations with concrete hit rate / false-hit rate projections.

**Prometheus metrics + live dashboard** — hit rate by type (exact / semantic), cost saved vs all-provider baseline, P95 latency by step, false-hit rate trend, judge usage rate.

**Cache invalidation** — by feature, tag, user_id, or entry_id, with full audit logging.

---

## Tech stack

| Layer | Choice |
|---|---|
| Proxy API | FastAPI (async, OpenAI-compatible) |
| Embeddings | OpenAI `text-embedding-3-small` / `sentence-transformers` |
| Vector store | Qdrant |
| Exact cache + TTL | Redis |
| Policy config | YAML (per-feature, validated against Pydantic schema) |
| Judge | GPT-4o-mini / Claude Haiku |
| Near-miss storage | PostgreSQL |
| Metrics | Prometheus + Grafana |
| Load testing | Locust |
| Containerization | Docker Compose |
| Language | Python 3.11+ |

---

## Project structure

```
semantic-cache-gateway/
├── contracts/               # shared Pydantic schemas — both tracks import from here
│   ├── proxy.py             # ProxyRequest, ProxyResponse
│   ├── cache.py             # CacheEntry
│   ├── decision.py          # CacheDecision
│   ├── policy.py            # PolicyConfig
│   └── near_miss.py         # NearMissLog
│
├── gateway/                 # Track A — proxy, cache store, provider adapters
│   ├── proxy.py
│   ├── store/
│   ├── adapters/
│   └── metrics.py
│
├── intelligence/            # Track B — embeddings, similarity, policy, judge
│   ├── embeddings.py
│   ├── similarity.py
│   ├── policy_loader.py
│   ├── judge.py
│   └── analysis.py
│
├── policies/                # YAML policy configs per feature
│   ├── faq_bot.yaml
│   ├── code_explainer.yaml
│   ├── live_news_summary.yaml
│   └── personal_account_assistant.yaml
│
├── dashboard/               # Streamlit metrics and ops dashboard
├── tests/
├── docker-compose.yml
├── seed_cache.py
└── requirements.txt
```

---

## Shared contract schemas

All data that crosses the boundary between the proxy layer and the intelligence layer is typed. Both tracks import from `contracts/` only — never from each other's implementation.

| Schema | Purpose |
|---|---|
| `ProxyRequest` | Incoming request shape — model, messages, feature_name, user_id, cache control flags |
| `CacheEntry` | Stored cache record — embedding, prompt hash, response, TTL, user scope, tags |
| `CacheDecision` | Decision attached to every response — hit type, similarity score, judge verdict, latency |
| `PolicyConfig` | Per-feature policy — threshold, TTL, user scoping, judge band, tag overrides |
| `NearMissLog` | Near-miss record — prompt hash, similarity score, threshold at time, judge rejection flag |

---

## Build phases

| Phase | Days | Focus |
|---|---|---|
| 0 | 1 | Shared contracts — schemas agreed, committed, both tracks unblocked |
| 1 | 2–4 | Proxy + exact cache (Track A) · Embedding engine + policy loader (Track B) |
| 2 | 4–7 | Leakage prevention + metrics (Track A) · Semantic matching + judge (Track B) |
| 3 | 7–10 | Load test harness + Prometheus (Track A) · Threshold tuning analysis (Track B) |
| 4 | 10–12 | Ops dashboard + invalidation UI (Track A) · ROI dashboard + recommendations (Track B) |
| 5 | 12–14 | Final simulation, Docker Compose, demo recording, narrative |

---

## Getting started

```bash
git clone https://github.com/<your-username>/semantic-cache-gateway.git
cd semantic-cache-gateway

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your API keys before running anything.

---

## Development approach

This project uses a **contract-first parallel build**. The proxy layer (Track A) and the intelligence layer (Track B) are developed independently against the shared `contracts/` schemas — neither track blocks on the other's implementation. Track A tests every route with curl/pytest using stub similarity responses. Track B tests embedding and policy logic against hardcoded `CacheEntry` stubs.

Integration happens at defined sync points between phases, not continuously.

---

## Interview narrative

> *"A policy-governed semantic cache gateway that reduces LLM cost and latency without compromising correctness, privacy, or auditability — with per-feature threshold tuning driven by real near-miss data."*

---

## License

MIT
