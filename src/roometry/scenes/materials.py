"""Material-role colors shared by the browser and diagnostic scene views."""

import json
from pathlib import Path

DEFAULT_MATERIAL_COLORS: dict[str, str] = json.loads(Path(__file__).with_name("material_colors.json").read_text())
