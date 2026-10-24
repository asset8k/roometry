"""Opt-in, single-run reconstruction check through the actual private HTTP API.

Run from the repository root. Requires explicit --allow-paid and configured
OPENAI_API_KEY. Questions are answered interactively; never guess missing facts.
Outputs contain private room data, so use an ignored artifacts/ subdirectory.
No extra model is called for evaluation, costing or rendering.
"""

import argparse
import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
import json
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

import httpx

from roometry.agents.state import AgentState
from roometry.config import Settings
from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase
from roometry.main import create_app
from roometry.optimization.schemas import OptimizationRun
from roometry.scenes.validation import validate_geometry
from roometry.vision.review_render import render_views

# Standard API USD / million tokens, verified 2026-10-24. Reads and writes are
# disjoint subsets of input. Write prices are total rates, not surcharges.
PRICES = {"gpt-6-luna": ("0.10", "0.01", "0.50", "0.125", "0.25"),
          "gpt-6.1-sol": ("2", "0.10", "10", "2.50", "5")}
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
        rates = next((prices for model, prices in PRICES.items() if entry["model"] == model or entry["model"].startswith(model + "-")), None)
        tokens = [entry.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens", "cache_write_tokens")]
        cost = None
        if rates is not None and all(type(value) is int and value >= 0 for value in tokens):
            incoming, cached, outgoing, written = tokens
            split = entry.get("cache_write_tokens_details")
            if split is None:
                short, long = written, 0
                entry["write_ttl_assumption"] = "30m default" if written else None
            else:
                short, long = split.get("ttl_30m_tokens"), split.get("ttl_12h_tokens")
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
    return {"currency": "USD", "pricing_date": "2026-10-24", "pricing_source": PRICE_SOURCE,
            "standard_rates_per_million": PRICES, "estimated_usd": str(total),
            "unpriced_calls": unknown, "complete_usage": unknown == 0,
            "note": "Estimate from provider-reported tokens at standard short-context rates; not a billing invoice. Unknown usage is excluded, so an incomplete total is a lower bound. Includes image and reasoning tokens in reported totals; no second charge for them.",
            "calls": rows}


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


async def optimize_once(client, state, directory, analyzer, focus):
    """One linked improvement; retry checks must reuse the completed execution."""
    if state.status not in {"completed", "partial"} or state.working_scene is None:
        save(directory, "optimization-skipped.json", {"reason": "Reconstruction did not retain a usable scene."})
        return
    if focus == "recommended":
        focus = state.room_assessment.recommended if state.room_assessment else None
        if focus is None:
            print("No category is recommended with sufficient confidence. Choose the one focus to test.", flush=True)
            focus = (await asyncio.to_thread(input, "Focus (openness/storage/workspace/aesthetics): ")).strip()
            if focus not in {"openness", "storage", "workspace", "aesthetics"}:
                raise ValueError("No valid focus selected; no improvement was started.")
    request = {"request_id": str(uuid4()), "focus": focus, "preferences": {}}
    save(directory, "optimization-request.json", request)
    prefix = f"/api/agent-runs/{state.run_id}/optimizations"
    before = len(analyzer.usage_records)
    prepared = await client.post(prefix, json=request)
    prepared.raise_for_status()
    assert len(analyzer.usage_records) == before
    save(directory, "optimization-prepared.json", prepared.json())
    print(f"Optimizing {focus}; original reconstruction is preserved…", flush=True)
    url = f"{prefix}/{prepared.json()['run_id']}"
    response = await client.post(url + "/execute", json={})
    response.raise_for_status()
    improvement = OptimizationRun.model_validate_json(response.text)
    save(directory, "optimization-run.json", response.json())
    before = len(analyzer.usage_records)
    assert (await client.post(prefix, json=request)).json() == response.json()
    assert (await client.post(url + "/execute", json={})).json() == response.json()
    assert (await client.get(url)).json() == response.json()
    original = await client.get(f"/api/agent-runs/{state.run_id}")
    original.raise_for_status()
    assert original.json() == state.model_dump(mode="json")
    assert improvement.source_scene == state.working_scene
    assert len(analyzer.usage_records) == before
    assert improvement.usage.model_calls <= improvement.limits.max_model_calls
    assert improvement.usage.tool_calls <= improvement.limits.max_tool_calls
    if improvement.result_scene:
        save(directory, "improved-geometry.json", validate_geometry(improvement.result_scene).model_dump(mode="json"))
        for view in render_views(improvement.result_scene):
            (directory / f"improved-{view.view_id}.png").write_bytes(view.image)
    save(directory, "flow-checks.json", {
        "original_preserved": True, "no_paid_calls_on_prepare_or_retries": True,
        "model_and_tool_limits_respected": True,
        "status": improvement.status, "focus": focus,
        "accepted_candidates": sum(a.outcome == "accepted" for a in improvement.attempts),
        "attempts": len(improvement.attempts),
    })
    print(f"Optimization {improvement.status}: {len(improvement.attempts)} candidates, {improvement.usage.model_calls} model calls", flush=True)


async def run(args):
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)  # Never replace an earlier run.
    settings = Settings()
    operations = ("analysis", "scene_proposal", "scene_repair", "visual_review", "visual_repair", "agent_decision", "room_assessment", "optimization_proposal", "optimization_review")
    expected = {op: "gpt-6.1-sol" if op == "scene_proposal" else "gpt-6-luna" for op in operations}
    if not settings.analysis_enabled or not settings.visual_review_enabled or not settings.openai_api_key:
        raise RuntimeError("Enable analysis and visual review, and configure the API key before this test.")
    if any(settings.model_route(op).model != model for op, model in expected.items()):
        raise RuntimeError("This check expects one Sol 6.1 construction and Luna for all other operations.")
    save(directory, "manifest.json", {"started_at": datetime.now(UTC).isoformat(),
         "photo_files": [path.name for path in args.photos], "room_type": args.room_type,
         "dimensions_m": [args.width, args.length, args.height],
         "provider_timeout_seconds": settings.analysis_timeout_seconds,
         "routes": {op: asdict(settings.model_route(op)) for op in operations},
         "cost_controls": {"deterministic_routing": settings.agent_deterministic_routing,
                           "compact_context": settings.scene_compact_context,
                           "construction_photo_target": settings.scene_photo_target,
                           "prompt_cache_mode": settings.model_prompt_cache_mode},
         "one_generation_only": True, "optimization_focus": getattr(args, "optimize", None),
         "reference_injected_into_prompts": False})
    case = ReconstructionCase.model_validate_json(args.case.read_text())
    started, responses, snapshots, answers = perf_counter(), [], [], []
    with TemporaryDirectory(prefix="roometry-live-") as temporary:
        settings.upload_storage_dir = Path(temporary) / "inputs"
        app = create_app(settings)
        service = app.state.analysis_service
        analyzer = service.analyzer
        original_request, original_propose = analyzer.request, service.propose

        async def capture_request(payload, *, operation):
            print(f"Calling {operation} ({settings.model_route(operation).model})…", flush=True)
            try:
                result = await original_request(payload, operation=operation)
                responses.append({"operation": operation, "response": result.model_dump(mode="json")})
                save(directory, "responses.json", responses)
                return result
            finally:
                save(directory, "cost.json", estimate_cost([asdict(record) for record in analyzer.usage_records], responses))
                if analyzer.usage_records:
                    record = analyzer.usage_records[-1]
                    print(f"Finished {operation}: {record.outcome}, {record.input_tokens} input / {record.output_tokens} output tokens", flush=True)

        async def capture_proposal(*positional, **keywords):
            result = await original_propose(*positional, **keywords)
            snapshots.append(result.model_dump(mode="json"))
            save(directory, "pre-review-proposals.json", snapshots)
            return result

        analyzer.request, service.propose = capture_request, capture_proposal
        try:
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    options = {"room_type": args.room_type, "goals": ["general_analysis"],
                               "dimensions": {"width_m": args.width, "length_m": args.length, "height_m": args.height, "measurements_source": "user_confirmed"}}
                    files = [("images", (path.name, path.read_bytes(), mimetypes.guess_type(path)[0] or "application/octet-stream")) for path in args.photos]
                    files.append(("options", (None, json.dumps(options))))
                    upload = await client.post("/api/rooms/inputs", files=files)
                    upload.raise_for_status()
                    save(directory, "upload.json", upload.json())
                    request = {"input_id": upload.json()["input_id"], "request_id": str(uuid4())}
                    save(directory, "start-request.json", request)
                    response = await client.post("/api/agent-runs", json=request)
                    while True:
                        response.raise_for_status()
                        state = AgentState.model_validate_json(response.text)
                        save(directory, "run.json", response.json())
                        print(f"Run {state.status}: {state.usage.model_calls} model calls", flush=True)
                        if state.status != "awaiting_answer":
                            break
                        question = state.pending_question.model_dump(mode="json")
                        print(json.dumps(question, ensure_ascii=False, indent=2), flush=True)
                        raw = await asyncio.to_thread(input, "Answer JSON (question_id + choice_id; optional dimensions): ")
                        answer = json.loads(raw)
                        answers.append({"question": question, "answer": answer})
                        save(directory, "clarifications.json", answers)
                        response = await client.post(f"/api/agent-runs/{state.run_id}/answer", json=answer)
                    # Read/start retries are idempotent and must not trigger paid work.
                    before = len(analyzer.usage_records)
                    assert (await client.post("/api/agent-runs", json=request)).json() == response.json()
                    assert (await client.get(f"/api/agent-runs/{state.run_id}")).json() == response.json()
                    assert len(analyzer.usage_records) == before
                    for label, proposal, scene in [("final", state.proposal, state.working_scene),
                        ("pre-review", None, None)]:
                        if label == "pre-review" and snapshots:
                            from roometry.vision.proposal_schemas import SceneProposalResponse
                            proposal = SceneProposalResponse.model_validate_json(json.dumps(snapshots[-1]))
                            scene = proposal.scene
                        report = evaluate_reconstruction(case, state.analysis, scene, unresolved=proposal.unresolved_entities if proposal else [])
                        save(directory, label + "-evaluation.json", report.model_dump(mode="json"))
                        if scene is not None:
                            save(directory, label + "-geometry.json", validate_geometry(scene).model_dump(mode="json"))
                            for view in render_views(scene):
                                (directory / f"{label}-{view.view_id}.png").write_bytes(view.image)
                    if getattr(args, "optimize", None):
                        await optimize_once(client, state, directory, analyzer, args.optimize)
                    print(json.dumps({"status": state.status, "objects": len(state.working_scene.objects) if state.working_scene else 0,
                          "openings": len(state.working_scene.openings) if state.working_scene else 0,
                          "elapsed_seconds": round(perf_counter()-started, 2)}, indent=2), flush=True)
        finally:
            save(directory, "cost.json", estimate_cost([asdict(record) for record in analyzer.usage_records], responses))
            save(directory, "timing.json", {"elapsed_seconds": round(perf_counter()-started, 2)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true", required=True)
    parser.add_argument("--photos", type=Path, nargs="+", required=True)
    parser.add_argument("--room-type", choices=["bedroom", "living_room"], default="bedroom")
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--length", type=float, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--optimize", choices=["recommended", "openness", "storage", "workspace", "aesthetics"],
                        help="Execute one improvement after reconstruction, using the same private session.")
    asyncio.run(run(parser.parse_args()))
