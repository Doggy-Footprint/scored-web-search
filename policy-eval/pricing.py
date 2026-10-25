"""List-price USD for the usage records a run leaves behind.

Rates are Anthropic first-party list prices per MTok, as of 2026-10 (claude-api skill, cached 2026-10-06).
A Claude Code subscription is not billed this way; the figure is what the same tokens would cost on the API.
"""

# input, output, cache write 5m, cache write 1h, cache read
RATES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 8.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.20, 0.01),
}
# Haiku 5.5 bills a prompt over 100K tokens at $0.50 / $2.50. The published note gives only input/output;
# the cache rates here are scaled by the same 5x, which is an assumption.
HAIKU_LONG = (0.50, 2.50, 0.625, 1.00, 0.05)
HAIKU_LONG_THRESHOLD = 100_000
WEB_SEARCH_USD = 10.00 / 1000  # per search; web fetch is not metered
ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


def canonical(model):
    """Map an alias or a dated/suffixed id onto a RATES key; None when the model is not priced."""
    if not model:
        return None
    m = ALIASES.get(model, model)
    for key in RATES:
        if m == key or m.startswith(key + "-") or m.startswith(key + "["):
            return key
    return None


def usage_usd(model, usage):
    """USD for one API response's `usage` object. Raises KeyError for an unpriced model."""
    key = canonical(model)
    if key is None:
        raise KeyError(f"no rate for model {model!r}")
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cread = usage.get("cache_read_input_tokens", 0) or 0
    cc = usage.get("cache_creation") or {}
    c5 = cc.get("ephemeral_5m_input_tokens")
    c1 = cc.get("ephemeral_1h_input_tokens")
    if c5 is None and c1 is None:
        c5, c1 = usage.get("cache_creation_input_tokens", 0) or 0, 0
    c5, c1 = c5 or 0, c1 or 0
    rates = RATES[key]
    if key == "claude-haiku-5-5" and inp + cread + c5 + c1 > HAIKU_LONG_THRESHOLD:
        rates = HAIKU_LONG
    r_in, r_out, r_c5, r_c1, r_cr = rates
    return (inp * r_in + out * r_out + c5 * r_c5 + c1 * r_c1 + cread * r_cr) / 1_000_000
