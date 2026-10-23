"""Cost the single live diagnostic without charging cached/reasoning twice."""

from decimal import Decimal

from scripts.live_reconstruction import estimate_cost


def test_actual_usage_costs_include_cached_discount_and_failed_output_tokens():
    report = estimate_cost([
        {"operation": "scene_proposal", "model": "gpt-6.1-sol-2026-10-15", "outcome": "incomplete",
         "input_tokens": 10_000, "cached_input_tokens": 2_000, "output_tokens": 4_000, "reasoning_tokens": 3_000},
        {"operation": "visual_review", "model": "gpt-6-luna", "outcome": "completed",
         "input_tokens": 20_000, "cached_input_tokens": 0, "output_tokens": 2_000, "reasoning_tokens": 1_000},
    ])
    # Sol: 8000*$2 + 2000*$0.10 + 4000*$10 per million; Luna: $0.003.
    assert Decimal(report["estimated_usd"]) == Decimal("0.0592")
    assert report["complete_usage"] and report["unpriced_calls"] == 0


def test_missing_usage_unknown_models_and_long_context_never_report_a_complete_cost():
    report = estimate_cost([
        {"model": "gpt-6-luna", "input_tokens": None, "cached_input_tokens": None, "output_tokens": None},
        {"model": "other-model", "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 100},
        {"model": "gpt-6.1-sol", "input_tokens": 280_000, "cached_input_tokens": 0, "output_tokens": 100},
    ])
    assert not report["complete_usage"] and report["unpriced_calls"] == 3
    assert all(row["estimated_usd"] is None for row in report["calls"])
