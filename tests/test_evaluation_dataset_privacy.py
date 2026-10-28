"""Public evaluation assets must not expose ignored personal inputs or outputs."""

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/evals"


def test_public_manifest_targets_are_consistent_and_paths_stay_public():
    manifest = json.loads((DATASET / "manifest.json").read_text())
    assert sum(manifest["target_splits"].values()) == manifest["target_case_count"]
    assert sum(manifest["target_room_types"].values()) == manifest["target_case_count"]
    assert sum(manifest["target_sources"].values()) == manifest["target_case_count"]
    assert len(manifest["cases"]) <= manifest["target_case_count"]
    for relative in manifest["public_paths"].values():
        path = DATASET / relative
        assert path.is_dir()
        assert path.resolve().is_relative_to(DATASET.resolve())
        assert not path.resolve().is_relative_to((DATASET / "private").resolve())


def test_git_excludes_nested_personal_assets_and_run_outputs_but_allows_public_cases():
    private = [
        "data/evals/private/images/room.jpg",
        "data/evals/private/cases/room.json",
        "data/evals/private/reference/room.json",
        "data/evals/private/nested/logs/run.json",
        "artifacts/evaluations/run/renders/room.png",
        "artifacts/evaluations/run/account.json",
        "data/evals/images/example/IMG_2201.HEIC",
        "data/evals/images/example/IMG_2205.jpg",
        "data/evals/bedroom_multiview.json",
        "data/evals/reference/bedroom_multiview.json",
        "data/fixtures/placement-regressions/bedroom_blocked.json",
        "data/fixtures/placement-regressions/bedroom_storage.json",
    ]
    public = [
        "data/evals/manifest.json",
        "data/evals/GUIDE.md",
        "data/evals/cases/living_01.json",
        "data/evals/images/living_01/living_01.jpg",
    ]
    # --no-index checks the rule even when a historical file is still in Git's
    # index pending deletion. It writes neither files nor the index.
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT, input="\n".join(private + public) + "\n",
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == set(private)


def test_current_publishable_data_contains_no_personal_room_fixtures_or_links():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "data"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    forbidden = (
        "IMG_2201", "IMG_2202", "IMG_2203", "IMG_2204", "IMG_2205",
        "bedroom_multiview", "bedroom_blocked", "bedroom_storage",
    )
    for relative in result.stdout.split("\0"):
        if not relative:
            continue
        path = ROOT / relative
        if not path.exists():
            continue  # Removed from the working tree; Git records the deletion.
        assert not path.is_symlink(), f"Dataset link needs explicit review: {relative}"
        assert not any(value.lower() in relative.lower() for value in forbidden)
        assert not path.resolve().is_relative_to((DATASET / "private").resolve())
        if path.suffix == ".json":
            content = path.read_text().lower()
            assert not any(value.lower() in content for value in forbidden), relative
