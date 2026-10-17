"""Shared demo scenes for edit-operation and API integration tests."""

from pathlib import Path

import pytest

from roometry.scenes.schemas import RoomScene

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/bedroom_scene.json"


@pytest.fixture
def bedroom_scene() -> RoomScene:
    return RoomScene.model_validate_json(FIXTURE.read_text())


@pytest.fixture
def supported_scene(bedroom_scene: RoomScene) -> RoomScene:
    """An off-center computer on a desk makes rotation propagation observable."""
    data = bedroom_scene.model_dump()
    desk = next(obj for obj in data["objects"] if obj["id"] == "desk_1")
    computer = next(obj for obj in data["objects"] if obj["id"] == "computer_1")
    desk.update(
        position={"x": 2.0, "y": 0.0, "z": 2.0},
        dimensions_m={"width": 2.0, "depth": 1.0, "height": 0.75},
        rotation_y_rad=0.0,
    )
    computer.update(
        position={"x": 2.5, "y": 0.75, "z": 2.1},
        dimensions_m={"width": 0.4, "depth": 0.3, "height": 0.45},
        rotation_y_rad=0.2,
    )
    data["objects"] = [desk, computer]
    data["openings"] = []
    return RoomScene.model_validate(data)
