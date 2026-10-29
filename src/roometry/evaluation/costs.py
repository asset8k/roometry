"""Offline token accounting at explicit, pinned rates; never a billing invoice."""

from decimal import Decimal

# Standard API USD / million tokens, verified 2026-10-28. Reads and writes are
# disjoint subsets of input. Write prices are total rates, not surcharges.
PRICES = {"gpt-6-luna": ("0.10", "0.01", "0.50", "0.125", "0.25"),
          "gpt-6.1-sol": ("2", "0.10", "10", "2.50", "5"),
          "gpt-6.1-terra": ("1", "0.05", "5", "1.25", "2.50")}
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"


def estimate_cost(records, responses=()):
    """Join complete provider usage by response ID; never infer a missing charge.

    Legacy ModelAttempt rows omit cache writes. They are unpriced without their
    full response. An explicit write count with no TTL split uses the documented
    default 30m used by this harness (which sends no cache retention override).
    """
    by_id = {item["response"]["id"]: item["response"] for item in responses}
    rows, total, unknown = [], Decimal(0), 0
    for entry in records:
        entry = dict(entry)
        response = by_id.get(entry.get("response_id"))
        if response is not None:
            usage = response.get("usage") or {}
            details = usage.get("input_tokens_details") or {}
            entry.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                         cached_input_tokens=details.get("cached_tokens"),
                         cache_write_tokens=details.get("cache_write_tokens"),
                         cache_write_tokens_details=details.get("cache_write_tokens_details"),
                         service_tier=response.get("service_tier"))
        name = entry.get("model")
        rates = next((prices for model, prices in PRICES.items() if isinstance(name, str) and (name == model or name.startswith(model + "-"))), None)
        tokens = [entry.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens", "cache_write_tokens")]
        cost = None
        if rates is not None and all(type(value) is int and value >= 0 for value in tokens):
            incoming, cached, outgoing, written = tokens
            split = entry.get("cache_write_tokens_details")
            if split is None:
                short, long = written, 0
                entry["write_ttl_assumption"] = "30m default" if written else None
            elif isinstance(split, dict):
                short, long = split.get("ttl_30m_tokens"), split.get("ttl_12h_tokens")
            else:
                short, long = None, None
            if (all(type(value) is int and value >= 0 for value in (short, long))
                and short + long == written and cached + written <= incoming < 272_000
                and entry.get("service_tier", "default") == "default"):
                a, b, c, d, e = map(Decimal, rates)
                entry["ordinary_input_tokens"] = incoming - cached - written
                cost = ((incoming-cached-written)*a + cached*b + outgoing*c + short*d + long*e) / 1_000_000
        if cost is None:
            unknown += 1
        else:
            total += cost
        rows.append({**entry, "estimated_usd": str(cost) if cost is not None else None})
    return {"currency": "USD", "pricing_date": "2026-10-28", "pricing_source": PRICE_SOURCE,
            "standard_rates_per_million": PRICES, "estimated_usd": str(total),
            "unpriced_calls": unknown, "complete_usage": unknown == 0,
            "note": "Estimate from provider-reported tokens at standard short-context rates; not a billing invoice. Unknown usage is excluded, so an incomplete total is a lower bound. Includes image and reasoning tokens in reported totals; no second charge for them.",
            "calls": rows}

