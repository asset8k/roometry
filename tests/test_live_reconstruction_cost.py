"""Cost the single live diagnostic without charging cached/reasoning twice."""

from decimal import Decimal
import pytest

from scripts.live_reconstruction import estimate_cost


def test_actual_usage_costs_include_cached_discount_and_failed_output_tokens():
    report = estimate_cost([
        {"operation": "scene_proposal", "model": "gpt-6.1-sol-2026-10-15", "outcome": "incomplete",
         "input_tokens": 10_000, "cached_input_tokens": 2_000, "cache_write_tokens": 0, "output_tokens": 4_000, "reasoning_tokens": 3_000},
        {"operation": "visual_review", "model": "gpt-6-luna", "outcome": "completed",
         "input_tokens": 20_000, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 2_000, "reasoning_tokens": 1_000},
    ])
    # Sol: 8000*$2 + 2000*$0.10 + 4000*$10 per million; Luna: $0.003.
    assert Decimal(report["estimated_usd"]) == Decimal("0.0592")
    assert report["complete_usage"] and report["unpriced_calls"] == 0


def test_missing_usage_unknown_models_and_long_context_never_report_a_complete_cost():
    report = estimate_cost([
        {"model": "gpt-6-luna", "input_tokens": None, "cached_input_tokens": None, "output_tokens": None},
        {"model": "other-model", "input_tokens": 100, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 100},
        {"model": "gpt-6.1-sol", "input_tokens": 280_000, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 100},
    ])
    assert not report["complete_usage"] and report["unpriced_calls"] == 3
    assert all(row["estimated_usd"] is None for row in report["calls"])


def test_full_response_usage_is_joined_by_id_and_prices_both_cache_lifetimes():
    records = [{"response_id": "sol", "model": "gpt-6.1-sol", "outcome": "incomplete"},
               {"response_id": "luna", "model": "gpt-6-luna", "outcome": "completed"}]
    responses = []
    for ident in ("luna", "sol"):  # Not request order.
        responses.append({"response": {"id": ident, "service_tier": "default", "usage": {
            "input_tokens": 10_000, "output_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 2000, "cache_write_tokens": 5000,
                "cache_write_tokens_details": {"ttl_30m_tokens": 3000, "ttl_12h_tokens": 2000}},
        }}})
    report = estimate_cost(records, responses)
    assert report["complete_usage"]
    assert Decimal(report["estimated_usd"]) == Decimal("0.035395")
    assert all(row["ordinary_input_tokens"] == 3000 for row in report["calls"])


def test_explicit_writes_without_split_use_disclosed_default_and_never_double_charge():
    report = estimate_cost([{"model": "gpt-6-luna", "input_tokens": 10_000,
        "cached_input_tokens": 1000, "cache_write_tokens": 8000, "output_tokens": 100}])
    assert Decimal(report["estimated_usd"]) == Decimal("0.00116")
    assert report["calls"][0]["write_ttl_assumption"] == "30m default"


@pytest.mark.parametrize("patch", [
    {"cache_write_tokens": None}, {"cache_write_tokens": 10_001},
    {"service_tier": "fast"},
    {"cache_write_tokens_details": {"ttl_30m_tokens": 1, "ttl_12h_tokens": 1}},
])
def test_unknown_writes_inconsistent_usage_and_nonstandard_tiers_stay_unpriced(patch):
    row = {"model": "gpt-6-luna", "input_tokens": 10_000,
           "cached_input_tokens": 0, "cache_write_tokens": 5000, "output_tokens": 100}
    report = estimate_cost([{**row, **patch}])
    assert not report["complete_usage"] and report["calls"][0]["estimated_usd"] is None
