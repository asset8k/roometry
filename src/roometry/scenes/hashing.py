"""Canonical identity for a specific scene revision, including its provenance."""

import hashlib
import json

from roometry.scenes.schemas import RoomScene


def scene_hash(scene: RoomScene) -> str:
    return hashlib.sha256(json.dumps(scene.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
