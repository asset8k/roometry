"""Goal shortlists preserve replacements and free-text catalog coverage."""

from types import SimpleNamespace

import pytest

from roometry.assessment.metrics import measure_room
from roometry.config import Settings
from roometry.optimization.inventory import catalog_options
from roometry.optimization.provider import OptimizationProvider
from roometry.scenes.scoring import STORAGE_TYPES
from tests.test_optimization_execution import policy_run
from tests.test_optimization_redesign import scenario, source


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instruction", [None, "Add a TV, remove a shelf and match the bed to the wardrobe."]
)
async def test_goal_catalog_is_compact_but_chat_can_still_add_a_tv(instruction):
    scene, _ = scenario("storage")
    run = policy_run(scene, "storage")
    run = run.model_copy(
        update={"instruction": instruction, "baseline_assessment": measure_room(scene)}
    )
    provider = OptimizationProvider(SimpleNamespace(settings=Settings(_env_file=None)))

    async def capture(model, name, data, instructions, images=()):
        return data

    provider._request = capture
    data = await provider.propose(run, source(), None)
    options = data["catalog_addition_options"]
    if instruction:
        tvs = [item for item in options if item["type"] == "tv"]
        assert tvs and any("wall" in item["placements"] for item in tvs)
        assert len(options) == len(
            catalog_options(
                instruction, required_ids=[o.asset_id for o in scene.objects]
            )
        )
    else:
        assert {item["type"] for item in options} <= (
            {o.type for o in scene.objects} | STORAGE_TYPES
        )
        assert len(options) < len(catalog_options())
    assert {o.asset_id for o in scene.objects if not o.fixed} <= {
        item["asset_id"] for item in options
    }
    assert data["scene"]["room"] == scene.room.model_dump(mode="json")
    assert (
        data["scene"]["openings"]
        == scene.model_dump(mode="json", exclude_none=True)["openings"]
    )
    assert "appearance_corrections" not in data["scene"]
    assert "parts" not in str(data["appearance_controls"])
