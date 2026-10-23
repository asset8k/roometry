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
from roometry.scenes.validation import validate_geometry
from roometry.vision.review_render import render_views

# Standard API USD / million tokens, verified 2026-10-23. Cached tokens are a
# subset of input tokens; reasoning tokens are already included in output.
PRICES = {"gpt-6-luna": ("0.10", "0.01", "0.50"), "gpt-6.1-sol": ("2", "0.10", "10")}
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"


def estimate_cost(records):
    rows, total, unknown = [], Decimal(0), 0
    for entry in records:
        rates = next((prices for model, prices in PRICES.items() if entry["model"] == model or entry["model"].startswith(model + "-")), None)
        tokens = [entry[key] for key in ("input_tokens", "cached_input_tokens", "output_tokens")]
        cost = None
        if rates is not None and all(value is not None for value in tokens):
            incoming, cached, outgoing = tokens
            if 0 <= cached <= incoming < 272_000 and outgoing >= 0:
                a, b, c = map(Decimal, rates)
                cost = ((incoming-cached)*a + cached*b + outgoing*c) / 1_000_000
        if cost is None:
            unknown += 1
        else:
            total += cost
        rows.append({**entry, "estimated_usd": str(cost) if cost is not None else None})
    return {"currency": "USD", "pricing_date": "2026-10-23", "pricing_source": PRICE_SOURCE,
            "standard_rates_per_million": PRICES, "estimated_usd": str(total),
            "unpriced_calls": unknown, "complete_usage": unknown == 0,
            "note": "Estimate from provider-reported tokens at standard short-context rates; not a billing invoice. Unknown usage is excluded, so an incomplete total is a lower bound. Includes image and reasoning tokens in reported totals; no second charge for them.",
            "calls": rows}


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


async def run(args):
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)  # Never replace an earlier run.
    settings = Settings()
    operations = ("analysis", "scene_proposal", "scene_repair", "visual_review", "visual_repair", "agent_decision")
    expected = {op: "gpt-6.1-sol" if op == "scene_proposal" else "gpt-6-luna" for op in operations}
    if not settings.analysis_enabled or not settings.visual_review_enabled or not settings.openai_api_key:
        raise RuntimeError("Enable analysis and visual review, and configure the API key before this test.")
    if any(settings.model_route(op).model != model for op, model in expected.items()):
        raise RuntimeError("This check expects one Sol 6.1 construction and Luna for all other operations.")
    save(directory, "manifest.json", {"started_at": datetime.now(UTC).isoformat(),
         "photo_files": [path.name for path in args.photos], "dimensions_m": [args.width, args.length, args.height],
         "routes": {op: asdict(settings.model_route(op)) for op in operations},
         "one_generation_only": True, "reference_injected_into_prompts": False})
    case = ReconstructionCase.model_validate_json(args.case.read_text())
    started, responses, snapshots = perf_counter(), [], []
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
                save(directory, "cost.json", estimate_cost([asdict(record) for record in analyzer.usage_records]))
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
                    options = {"room_type": "bedroom", "goals": ["better_aesthetics"],
                               "dimensions": {"width_m": args.width, "length_m": args.length, "height_m": args.height, "measurements_source": "user_confirmed"}}
                    files = [("images", (path.name, path.read_bytes(), "image/heic")) for path in args.photos]
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
                    print(json.dumps({"status": state.status, "objects": len(state.working_scene.objects) if state.working_scene else 0,
                          "openings": len(state.working_scene.openings) if state.working_scene else 0,
                          "elapsed_seconds": round(perf_counter()-started, 2)}, indent=2), flush=True)
        finally:
            save(directory, "cost.json", estimate_cost([asdict(record) for record in analyzer.usage_records]))
            save(directory, "timing.json", {"elapsed_seconds": round(perf_counter()-started, 2)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-paid", action="store_true", required=True)
    parser.add_argument("--photos", type=Path, nargs="+", required=True)
    parser.add_argument("--width", type=float, required=True)
    parser.add_argument("--length", type=float, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
