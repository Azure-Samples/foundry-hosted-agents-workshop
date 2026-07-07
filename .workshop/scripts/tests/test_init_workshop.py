"""Tests for ``scripts/init_workshop.py``.

The init script is invoked from two GitHub Actions workflows, so the tests pin
its three contract guarantees: it renders + substitutes correctly when missing
the marker, it creates the marker, and it no-ops once the marker exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.init_workshop as init_workshop


def _write_partials(partials_dir: Path) -> None:
    partials_dir.mkdir(parents=True, exist_ok=True)
    (partials_dir / "_header.md").write_text(
        "# Header step {{STEP_NUMBER}} {{STEP_TITLE}}\n"
        "\n"
        "<!-- step: {{STEP_NUMBER}} -->\n"
        "\n"
        "{{WORKSHOP_MAP}}\n",
        encoding="utf-8",
    )
    (partials_dir / "_start_button.md").write_text(
        "Start the workshop Step {{NEXT_STEP_NUMBER}} "
        "https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/start-workshop.yml\n",
        encoding="utf-8",
    )
    (partials_dir / "_push_to_advance.md").write_text(
        "Push to advance to Step {{NEXT_STEP_NUMBER}} "
        "(git push) from step {{CURRENT_STEP}}\n",
        encoding="utf-8",
    )


def _write_step_doc(steps_dir: Path, step: int) -> None:
    steps_dir.mkdir(parents=True, exist_ok=True)
    (steps_dir / f"{step:02d}-synthetic.md").write_text(
        f"<!-- step: {step} -->\n\n# Step {step} body\n",
        encoding="utf-8",
    )


@pytest.fixture
def workshop_repo(tmp_path, monkeypatch):
    """Build a minimal repo tree with partials, docs, state, and a stale README."""

    partials = tmp_path / ".workshop" / "docs" / "partials"
    steps = tmp_path / ".workshop" / "docs" / "steps"
    _write_partials(partials)
    for step in (0, 1, 9, 99):
        _write_step_doc(steps, step)

    (tmp_path / ".workshop_instance").mkdir()
    (tmp_path / ".workshop_instance" / ".workshop-state.json").write_text(
        json.dumps({"current_step": 0, "schema_version": 1}) + "\n",
        encoding="utf-8",
    )
    # Pre-init README still contains literal placeholders (mirrors the template).
    (tmp_path / "README.md").write_text(
        "Old README with https://github.com/{{OWNER}}/{{REPO}}/actions placeholder.\n",
        encoding="utf-8",
    )
    # A nested doc with placeholders that the renderer does not write.
    nested = tmp_path / ".workshop" / "docs" / "steps"
    (nested / "00-synthetic.md").write_text(
        "<!-- step: 0 -->\n\n# Step 0 body — see {{OWNER}}/{{REPO}} for source.\n",
        encoding="utf-8",
    )
    (tmp_path / ".github").mkdir()

    monkeypatch.setattr(init_workshop, "REPO_ROOT", tmp_path)
    # Patch the render function's own globals (matches test_advance_step.py).
    # init_workshop imports render via `from render_readme import render` after a
    # sys.path insertion, which loads render_readme as a top-level module — that
    # is a separate object from `scripts.render_readme`. Patching the function's
    # __globals__ is module-identity-agnostic and bullet-proof.
    monkeypatch.setitem(init_workshop.render.__globals__, "PARTIALS_DIR", partials)
    monkeypatch.setitem(init_workshop.render.__globals__, "STEPS_DIR", steps)
    return tmp_path


def test_initialize_renders_readme_with_real_owner_and_repo(workshop_repo):
    result = init_workshop.initialize(owner="octo", repo="demo")

    assert result == 0
    readme = (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/octo/demo/actions/workflows/start-workshop.yml" in readme
    assert "{{OWNER}}" not in readme
    assert "{{REPO}}" not in readme


def test_initialize_substitutes_placeholders_in_nested_docs(workshop_repo):
    init_workshop.initialize(owner="octo", repo="demo")

    nested = (workshop_repo / ".workshop" / "docs" / "steps" / "00-synthetic.md").read_text(encoding="utf-8")
    assert "octo/demo" in nested
    assert "{{OWNER}}" not in nested
    assert "{{REPO}}" not in nested


def test_initialize_creates_marker_file(workshop_repo):
    assert not (workshop_repo / ".workshop_instance" / ".workshop-initialized").exists()

    init_workshop.initialize(owner="octo", repo="demo")

    assert (workshop_repo / ".workshop_instance" / ".workshop-initialized").exists()


def test_initialize_is_idempotent_when_marker_present(workshop_repo, capsys):
    # First run sets things up.
    init_workshop.initialize(owner="octo", repo="demo")
    readme_after_first = (workshop_repo / "README.md").read_text(encoding="utf-8")
    nested_after_first = (workshop_repo / ".workshop" / "docs" / "steps" / "00-synthetic.md").read_text(
        encoding="utf-8"
    )

    # Second run with different owner/repo must be a no-op because the marker is present.
    result = init_workshop.initialize(owner="someone-else", repo="other-repo")
    captured = capsys.readouterr()

    assert result == 0
    assert "already initialized" in captured.out
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == readme_after_first
    assert (
        workshop_repo / ".workshop" / "docs" / "steps" / "00-synthetic.md"
    ).read_text(encoding="utf-8") == nested_after_first


def test_dry_run_does_not_modify_files_or_create_marker(workshop_repo, capsys):
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")
    original_nested = (workshop_repo / ".workshop" / "docs" / "steps" / "00-synthetic.md").read_text(
        encoding="utf-8"
    )

    result = init_workshop.initialize(owner="octo", repo="demo", dry_run=True)
    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN" in captured.out
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert (
        workshop_repo / ".workshop" / "docs" / "steps" / "00-synthetic.md"
    ).read_text(encoding="utf-8") == original_nested
    assert not (workshop_repo / ".workshop_instance" / ".workshop-initialized").exists()


def test_initialize_rejects_placeholder_owner_or_repo(workshop_repo, capsys):
    result = init_workshop.main(["--owner", "{{OWNER}}", "--repo", "demo"])
    captured = capsys.readouterr()

    assert result == 1
    assert "cannot be the literal placeholder" in captured.err
    assert not (workshop_repo / ".workshop_instance" / ".workshop-initialized").exists()


def test_main_returns_nonzero_when_state_file_missing(workshop_repo, capsys):
    (workshop_repo / ".workshop_instance" / ".workshop-state.json").unlink()

    result = init_workshop.main(["--owner", "octo", "--repo", "demo"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Missing .workshop_instance/.workshop-state.json" in captured.err
    assert not (workshop_repo / ".workshop_instance" / ".workshop-initialized").exists()
