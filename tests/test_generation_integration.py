"""The public upload/question/review/edit contract, with no paid model calls."""

from copy import deepcopy
from math import pi
from uuid import uuid4

import pytest

from roometry.agents.state import AgentState
from tests import test_vision_api as api
from tests.test_agent_clarification import measurements
from tests.test_agent_tools import decision, photo
from tests.test_scene_proposals import draft_data, scene_action
from tests.test_visual_review import discrepancy, review_action

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("corrected", [False, True])
async def test_five_photos_interrupt_resume_review_and_edit_through_http(harness, corrected):
    app, client, calls, actions = harness
    app.state.settings.visual_review_enabled = True
    uploaded = await api.upload(client, count=5)
    start = {"input_id": uploaded["input_id"], "request_id": str(uuid4())}
    actions.extend([decision("analyze_photos"), photo, decision("propose_scene")])
    paused_response = await client.post("/api/agent-runs", json=start)
    assert paused_response.status_code == 200
    paused = paused_response.json()
    assert paused["status"] == "awaiting_answer"
    assert len(paused["image_ids"]) == 5
    assert len(calls) == 3
    actions.extend([decision("propose_scene"), scene_action(), review_action(discrepancy if corrected else None)])
    if corrected:
        bed = deepcopy(draft_data()["objects"][0])
        bed["rotation_y_rad"] = pi
        actions.append(scene_action({"wall_assignments": [], "openings": [], "objects": [bed], "assumptions": [], "warnings": []}))
    actions.append(decision("finish"))
    answer = measurements(paused["pending_question"]["id"])
    response = await client.post(f"/api/agent-runs/{paused['run_id']}/answer", json=answer)
    assert response.status_code == 200, response.text
    complete = response.json()
    state = AgentState.model_validate_json(response.text)
    assert state.status == ("partial" if corrected else "completed")
    assert state.base_scene == state.working_scene == state.proposal.scene
    assert state.analysis.model_dump(mode="json") == paused["analysis"]
    assert state.proposal.visual_review.repair == ("accepted" if corrected else "not_needed")
    assert state.base_scene.room.width_m == state.base_scene.room.length_m == 4
    assert state.base_scene.room.height_m == 2.6
    assert state.base_scene.objects[0].rotation_y_rad == pytest.approx(pi if corrected else 0)
    ledger = app.state.analysis_service.analyzer.usage_records
    assert sum(entry.operation == "analysis" for entry in ledger) == 1
    assert sum(entry.operation == "scene_proposal" for entry in ledger) == 1
    assert sum(entry.operation == "visual_review" for entry in ledger) == 1
    assert state.usage.model_calls == len(ledger) == len(calls)
    for payload, attempt in zip(calls, ledger, strict=True):
        if payload.get("text", {}).get("format", {}).get("name") in {"room_observations", "room_scene_draft", "room_visual_review"}:
            image_parts = [part for part in payload["input"][0]["content"] if part["type"] == "input_image"]
            assert len(image_parts) == (8 if attempt.operation in {"visual_review", "visual_repair"} else 5)
    paid_count = len(calls)
    # Replayed starts, reads and answers must not pay for analysis/review again.
    assert (await client.post("/api/agent-runs", json=start)).json() == complete
    assert (await client.get(f"/api/agent-runs/{state.run_id}")).json() == complete
    assert (await client.post(f"/api/agent-runs/{state.run_id}/answer", json=answer)).json() == complete
    assert len(calls) == paid_count
    assert response.headers["cache-control"] == "no-store"
    # The reviewed (including provisional) reconstruction is usable by the editor.
    preview = await client.post("/api/scenes/preview-edit", json={
        "scene": complete["working_scene"],
        "edit": {"operation": "move", "object_id": "chair_a", "position": {"x": 2.4, "z": 2.4}},
    })
    assert preview.status_code == 200 and preview.json()["accepted"], preview.text
    assert preview.json()["scene"]["room"] == complete["working_scene"]["room"]
    assert (await client.get(f"/api/agent-runs/{state.run_id}")).json() == complete
    assert len(calls) == paid_count and not actions
