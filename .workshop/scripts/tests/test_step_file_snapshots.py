import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_step_zero_ships_requirements():
    """Step 0 is the source of truth for the Python packages."""
    assert (REPO_ROOT / ".workshop" / "step_files" / "00" / "requirements.txt").exists()


def test_overlaying_step_one_preserves_step_zero_requirements(tmp_path):
    """Advancing is incremental: laying step 1 on top of step 0 must not drop
    requirements.txt. This guards against the old wipe-and-replace regression
    where a file missing from the next snapshot got deleted."""
    travel_assistant = tmp_path / "travel_assistant"

    # Simulate a learner who has completed step 0 (requirements.txt present).
    shutil.copytree(REPO_ROOT / ".workshop" / "step_files" / "00", travel_assistant)
    requirements = travel_assistant / "requirements.txt"
    assert requirements.exists()

    # Advance overlays step 1 without clearing the folder first.
    shutil.copytree(REPO_ROOT / ".workshop" / "step_files" / "01", travel_assistant, dirs_exist_ok=True)

    # requirements.txt carried forward, and the step 1 scaffolds arrived.
    assert requirements.exists()
    assert requirements.read_bytes() == (
        REPO_ROOT / ".workshop" / "step_files" / "00" / "requirements.txt"
    ).read_bytes()
    for scaffold in ("agent.yaml", "agent.manifest.yaml", "main.py"):
        assert (travel_assistant / scaffold).exists()
