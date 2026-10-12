from prometheus_client import Counter, Histogram


CACHE_RESPONSES = Counter(
    "gateway_cache_responses_total",
    "Successful chat responses by cache outcome",
    ["hit_type"],
)

CACHE_BYPASSES = Counter(
    "gateway_cache_bypasses_total",
    "Requests that bypassed cache according to request or policy rules",
)

CACHE_ERRORS = Counter(
    "gateway_cache_errors_total",
    "Cache and embedding failures",
    ["operation"],
)

PROVIDER_CALLS = Counter(
    "gateway_provider_calls_total",
    "Provider call attempts",
)

PROVIDER_ERRORS = Counter(
    "gateway_provider_errors_total",
    "Failed provider calls",
)

CACHE_LOOKUP_SECONDS = Histogram(
    "gateway_cache_lookup_seconds",
    "Time spent evaluating cache eligibility and looking up responses",
)

PROVIDER_SECONDS = Histogram(
    "gateway_provider_seconds",
    "Time spent waiting for provider calls",
)

REQUEST_SECONDS = Histogram(
    "gateway_request_seconds",
    "Total chat request time",
)