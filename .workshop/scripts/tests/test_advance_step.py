import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.advance_step as advance_step


def _step_dir_name(step: int) -> str:
    return str(step) if step == 99 else f"{step:02d}"


def _write_step_doc(steps_dir: Path, step: int) -> None:
    steps_dir.mkdir(parents=True, exist_ok=True)
    (steps_dir / f"{_step_dir_name(step)}-synthetic.md").write_text(
        f"<!-- step: {step} -->\n\n# Synthetic step {step}\n",
        encoding="utf-8",
    )


def _write_state(repo: Path, current_step: int) -> None:
    (repo / ".workshop_instance" / ".workshop-state.json").write_text(
        json.dumps({"current_step": current_step, "schema_version": 1}) + "\n",
        encoding="utf-8",
    )


def _write_readme(repo: Path, step: int) -> None:
    (repo / "README.md").write_text(
        f"<!-- step: {step} -->\n\n# Current step {step}\n",
        encoding="utf-8",
    )


def _create_step_files(repo: Path, step: int, content: str) -> None:
    target = repo / ".workshop" / "step_files" / _step_dir_name(step)
    target.mkdir(parents=True, exist_ok=True)
    (target / "stub.txt").write_text(content, encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in ``repo``, raising on non-zero exit."""

    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


_requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="requires the `git` binary on PATH",
)


def _git_init_with_identity(repo: Path) -> None:
    """Initialize ``repo`` with a deterministic git identity."""

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".keep_initial").write_text("seed", encoding="utf-8")
    _git(repo, "add", ".keep_initial")
    _git(repo, "commit", "-q", "-m", "seed")


@pytest.fixture
def workshop_repo(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    partials = docs / "partials"
    steps = docs / "steps"
    partials.mkdir(parents=True)
    (partials / "_header.md").write_text(
        "# Header {{STEP_NUMBER}} {{STEP_TITLE}}\n\n<!-- step: {{STEP_NUMBER}} -->\n\n{{WORKSHOP_MAP}}\n",
        encoding="utf-8",
    )
    (partials / "_start_button.md").write_text(
        "<!-- workshop-footer: start-workshop -->\n"
        "Start the workshop Step {{NEXT_STEP_NUMBER}} from {{CURRENT_STEP}} "
        "in {{OWNER}}/{{REPO}} (start-workshop.yml)\n",
        encoding="utf-8",
    )
    (partials / "_push_to_advance.md").write_text(
        "<!-- workshop-footer: push-to-advance -->\n"
        "Push to Step {{NEXT_STEP_NUMBER}} from {{CURRENT_STEP}} "
        "in {{OWNER}}/{{REPO}} (git push)\n",
        encoding="utf-8",
    )
    # Lay down step doc files for every workshop step plus cleanup so the full
    # 0 -> 9 -> 99 walkthrough renders without missing-step errors.
    for step in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 99):
        _write_step_doc(steps, step)

    (tmp_path / "travel_assistant").mkdir()
    (tmp_path / ".workshop" / "step_files").mkdir(parents=True)
    (tmp_path / ".workshop_instance" / "workshop_backups").mkdir(parents=True)

    monkeypatch.setattr(advance_step, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(advance_step.render.__globals__, "PARTIALS_DIR", partials)
    monkeypatch.setitem(advance_step.render.__globals__, "STEPS_DIR", steps)
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    return tmp_path


def test_happy_path_advances_and_exports_new_step(workshop_repo, monkeypatch):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("old", encoding="utf-8")
    _create_step_files(workshop_repo, 1, "new step 1")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    result = advance_step.main(["--expected", "0"])

    assert result == 0
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 1
    assert "# Synthetic step 1" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0" / "stub.txt").read_text(encoding="utf-8") == "old"
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "new step 1"
    assert github_env.read_text(encoding="utf-8") == "NEW_STEP=1\n"


def test_expected_mismatch_exits_nonzero_without_writes(workshop_repo, capsys):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("old", encoding="utf-8")
    original_state = (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--expected", "1"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Expected step 1 but the repo is on step 2" in captured.err
    assert (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8") == original_state
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert not (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-2").exists()


def test_state_desync_exits_nonzero(workshop_repo, capsys):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 3)

    result = advance_step.main(["--expected", "2"])
    captured = capsys.readouterr()

    assert result == 1
    assert (
        "State desync: .workshop-state.json says step 2 but README.md says step 3. Repair manually."
        in captured.err
    )


def test_step_nine_advances_to_final_cleanup(workshop_repo):
    _write_state(workshop_repo, 9)
    _write_readme(workshop_repo, 9)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 9 work", encoding="utf-8")
    _create_step_files(workshop_repo, 99, "cleanup")

    result = advance_step.main(["--expected", "9"])

    assert result == 0
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 99
    readme = (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert "# Synthetic step 99" in readme
    assert "Advance to Step" not in readme
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "cleanup"


def test_already_complete_exits_nonzero(workshop_repo, capsys):
    _write_state(workshop_repo, 99)
    _write_readme(workshop_repo, 99)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("complete", encoding="utf-8")

    result = advance_step.main(["--expected", "99"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Workshop already complete. Use Reset workshop to restart." in captured.err
    assert not (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-99").exists()


def test_missing_step_files_warns_and_keeps_travel_assistant(workshop_repo, capsys):
    _write_state(workshop_repo, 4)
    _write_readme(workshop_repo, 4)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("keep me", encoding="utf-8")

    result = advance_step.main(["--expected", "4"])
    captured = capsys.readouterr()

    assert result == 0
    assert "WARNING: no .workshop/step_files/05 to lay down" in captured.out
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 5
    assert "# Synthetic step 5" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "keep me"
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-4" / "stub.txt").read_text(encoding="utf-8") == "keep me"


def test_empty_travel_assistant_skips_backup(workshop_repo):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    _create_step_files(workshop_repo, 1, "new step")

    result = advance_step.main(["--expected", "0"])

    assert result == 0
    assert not (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0").exists()
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "new step"


def test_dry_run_has_no_side_effects(workshop_repo, capsys, monkeypatch):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("old", encoding="utf-8")
    _create_step_files(workshop_repo, 1, "new step 1")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    original_state = (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--expected", "0", "--dry-run"])
    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN: advancing step 0 -> 1" in captured.out
    assert (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8") == original_state
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "old"
    assert not (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0").exists()
    assert not github_env.exists()


def test_advance_preserves_prior_step_files(workshop_repo):
    """Advancing is incremental: files from earlier steps are preserved, not wiped."""
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("old", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "leftover.py").write_text("# stale\n", encoding="utf-8")
    _create_step_files(workshop_repo, 1, "new step 1")

    result = advance_step.main(["--expected", "0"])

    assert result == 0
    # Files present in the new step snapshot are overlaid/updated in place.
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "new step 1"
    # Files carried forward from the prior step survive the advance (incremental).
    assert (workshop_repo / "travel_assistant" / "leftover.py").read_text(encoding="utf-8") == "# stale\n"
    # A backup is still taken before laying down the next step.
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0" / "leftover.py").exists()


def test_reset_backs_up_and_returns_to_step_zero(workshop_repo):
    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 5 work", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "old.txt").write_text("remove", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "reset starter")

    result = advance_step.main(["--reset"])

    assert result == 0
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 0
    assert "# Synthetic step 0" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    backups = list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("reset-*"))
    assert len(backups) == 1
    assert (backups[0] / "stub.txt").read_text(encoding="utf-8") == "step 5 work"
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "reset starter"
    assert not (workshop_repo / "travel_assistant" / "old.txt").exists()


def test_init_lays_down_step_zero_without_backup(workshop_repo, monkeypatch):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    # Template-fresh state: only the .gitkeep placeholder. We deliberately do
    # NOT seed an additional sentinel and assert it is gone — that assertion
    # would depend on what step_files/00/ ships, not on what --init does.
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "step 0 starter")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    result = advance_step.main(["--init"])

    assert result == 0
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 0
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 0 starter"
    assert not list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("*"))
    assert "# Synthetic step 0" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert github_env.read_text(encoding="utf-8") == "NEW_STEP=0\n"


def test_init_does_not_require_expected_flag(workshop_repo):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 0, "starter")

    # No --expected supplied; --init bypasses the advance guard.
    result = advance_step.main(["--init"])

    assert result == 0


def test_init_fails_when_state_is_not_step_zero(workshop_repo, capsys):
    _write_state(workshop_repo, 3)
    _write_readme(workshop_repo, 3)
    (workshop_repo / "travel_assistant" / "work.py").write_text("keep", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "starter")

    result = advance_step.main(["--init"])
    captured = capsys.readouterr()

    assert result == 1
    assert "Cannot init: workshop is on step 3" in captured.err
    # Participant work untouched.
    assert (workshop_repo / "travel_assistant" / "work.py").read_text(encoding="utf-8") == "keep"
    assert not list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("*"))


def test_init_dry_run_has_no_side_effects(workshop_repo, capsys, monkeypatch):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "starter")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--init", "--dry-run"])
    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN: applying step 0" in captured.out
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert (workshop_repo / "travel_assistant" / ".gitkeep").exists()
    assert not (workshop_repo / "travel_assistant" / "stub.txt").exists()
    assert not github_env.exists()


def test_init_fails_when_step_files_missing(workshop_repo, capsys):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    # Intentionally do NOT create step_files/00/.

    result = advance_step.main(["--init"])
    captured = capsys.readouterr()

    assert result == 1
    assert "missing .workshop/step_files/00" in captured.err
    # State and README must be left untouched so the marker is not created.
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 0
    assert (workshop_repo / "travel_assistant" / ".gitkeep").exists()


def test_init_and_reset_are_mutually_exclusive(workshop_repo, capsys):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)

    with pytest.raises(SystemExit):
        advance_step.main(["--init", "--reset"])
    captured = capsys.readouterr()
    assert "not allowed with argument" in captured.err or "argument --reset" in captured.err


def test_init_refuses_when_travel_assistant_has_user_content(workshop_repo, capsys, monkeypatch):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "my_notes.py").write_text("# user edits", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "starter")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    original_state = (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--init"])
    captured = capsys.readouterr()

    assert result == 1
    assert "differ from the step 0 starter set" in captured.err
    assert "Use --reset" in captured.err
    # User content and workshop state must be untouched so the marker is not created.
    assert (workshop_repo / "travel_assistant" / "my_notes.py").read_text(encoding="utf-8") == "# user edits"
    assert not (workshop_repo / "travel_assistant" / "stub.txt").exists()
    assert (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8") == original_state
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert not github_env.exists()
    assert not list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("*"))


def test_init_dry_run_refuses_when_travel_assistant_has_user_content(workshop_repo, capsys):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "my_notes.py").write_text("# user edits", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "starter")

    result = advance_step.main(["--init", "--dry-run"])
    captured = capsys.readouterr()

    assert result == 1
    assert "differ from the step 0 starter set" in captured.err
    # The dry-run plan must NOT be printed when the safety guard refuses.
    assert "DRY RUN: applying step 0" not in captured.out
    assert (workshop_repo / "travel_assistant" / "my_notes.py").read_text(encoding="utf-8") == "# user edits"


def test_init_succeeds_when_travel_assistant_matches_step_zero(workshop_repo, monkeypatch):
    """Idempotent retry: --init succeeds when travel_assistant/ already mirrors step_files/00/.

    Covers the CI crash-recovery case where --init laid files down but the
    follow-up init_workshop.py crashed before creating the marker, so the next
    workflow run sees marker-absent + travel_assistant/ already populated.
    """

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 0, "starter")
    # Mirror step_files/00/ into travel_assistant/ exactly.
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("starter", encoding="utf-8")

    result = advance_step.main(["--init"])

    assert result == 0
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 0
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "starter"
    assert not list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("*"))


# === Fully-local workshop flow (issue #5) ===


def test_full_local_walkthrough_0_to_99(workshop_repo):
    """Walk 0 -> 1 -> ... -> 9 -> 99 entirely via the local --expected-current-step alias."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    for step in range(1, 10):
        _create_step_files(workshop_repo, step, f"canonical step {step}")
    _create_step_files(workshop_repo, 99, "canonical cleanup")

    for current in range(0, 9):
        next_step = current + 1
        result = advance_step.main(["--expected-current-step", str(current)])
        assert result == 0, f"advance from step {current} failed"
        state = json.loads(
            (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
        )
        assert state["current_step"] == next_step
        readme = (workshop_repo / "README.md").read_text(encoding="utf-8")
        assert f"# Synthetic step {next_step}" in readme
        stub = (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8")
        assert stub == f"canonical step {next_step}"

    # Step 9 -> step 99 (the cleanup jump).
    result = advance_step.main(["--expected-current-step", "9"])
    assert result == 0
    final_state = json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )
    assert final_state["current_step"] == 99
    final_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert "# Synthetic step 99" in final_readme
    assert "Advance to Step" not in final_readme
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(
        encoding="utf-8"
    ) == "canonical cleanup"

    # Calling advance from the final step must error cleanly.
    result = advance_step.main(["--expected-current-step", "99"])
    assert result == 1


def test_expected_optional_skips_guard_locally(workshop_repo, capsys):
    """Local users may omit --expected; the script reports and advances."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1")

    result = advance_step.main([])
    captured = capsys.readouterr()

    assert result == 0
    assert "No --expected guard provided; advancing from current step 0." in captured.out
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1


def test_expected_alias_matches_canonical_flag(workshop_repo):
    """--expected-current-step must behave identically to --expected."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1")

    result = advance_step.main(["--expected-current-step", "0"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1


def test_empty_expected_string_still_fails(workshop_repo, capsys):
    """An explicit empty string must not silently bypass the guard."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1")

    result = advance_step.main(["--expected", ""])
    captured = capsys.readouterr()

    assert result == 1
    assert "--expected must be an integer" in captured.err
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 0


@_requires_git
def test_auto_commit_creates_commit_with_workshop_paths_only(workshop_repo):
    """--auto-commit must stage workshop paths only, leaving unrelated edits alone."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1 content")
    _git_init_with_identity(workshop_repo)
    # Sentinel that must remain untracked after auto-commit.
    (workshop_repo / "untracked_local_edit.txt").write_text("private", encoding="utf-8")

    result = advance_step.main(["--expected-current-step", "0", "--auto-commit"])

    assert result == 0
    log = _git(workshop_repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: advance to step 1"
    status = _git(workshop_repo, "status", "--porcelain").stdout
    assert "?? untracked_local_edit.txt" in status
    # The test created .workshop/step_files/01/ (the canonical starter the
    # script reads from) but it isn't in the workshop-owned auto-commit
    # pathspec, so it must stay untracked after --auto-commit.
    assert any(line.startswith("?? .workshop/") for line in status.splitlines()) or \
        ".workshop/step_files/" in status


@_requires_git
def test_auto_commit_dry_run_is_a_noop(workshop_repo, capsys):
    """--auto-commit must not touch git history when combined with --dry-run."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1")
    _git_init_with_identity(workshop_repo)
    head_before = _git(workshop_repo, "rev-parse", "HEAD").stdout.strip()

    result = advance_step.main(
        ["--expected-current-step", "0", "--auto-commit", "--dry-run"]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "Would auto-commit" in captured.out
    head_after = _git(workshop_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before
    # State must be unchanged because dry-run skipped the rewrite too.
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 0


@_requires_git
def test_reset_auto_commit_creates_reset_commit(workshop_repo):
    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 5 work", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "reset starter")
    _git_init_with_identity(workshop_repo)

    result = advance_step.main(["--reset", "--auto-commit"])

    assert result == 0
    log = _git(workshop_repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: reset to step 0"


@_requires_git
def test_reset_auto_commit_stages_deleted_root_overlay(workshop_repo):
    """A reset commit must stage the removal of tracked root-overlay dirs.

    Regression: _auto_commit() used to skip overlay targets that no longer
    existed on disk, so a dir deleted by --reset (e.g. travel_toolbox/) was
    left unstaged and the reset commit was incomplete.
    """

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 2 work", encoding="utf-8")
    _create_step_files(workshop_repo, 0, "reset starter")
    # A later step declares the overlay target so reset discovers and clears it.
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "declared")
    # The learner reached that step, so the overlay dir exists and is tracked.
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "toolbox.yaml").write_text("live", encoding="utf-8")
    _git_init_with_identity(workshop_repo)
    _git(workshop_repo, "add", "travel_toolbox")
    _git(workshop_repo, "commit", "-q", "-m", "learner reached step 2")

    result = advance_step.main(["--reset", "--auto-commit"])

    assert result == 0
    log = _git(workshop_repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: reset to step 0"
    # The deletion must be committed: the dir is gone from the index and the
    # working tree is clean with respect to it.
    tracked = _git(workshop_repo, "ls-files", "travel_toolbox").stdout.strip()
    assert tracked == "", "reset commit should remove travel_toolbox/ from the index"
    status = _git(workshop_repo, "status", "--porcelain").stdout
    assert "travel_toolbox" not in status


def test_auto_commit_outside_git_repo_warns_and_keeps_changes(workshop_repo, capsys):
    """When the user runs --auto-commit outside a git repo, we warn and keep file changes."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    _create_step_files(workshop_repo, 1, "step 1")
    # Intentionally no git init in workshop_repo.

    result = advance_step.main(["--expected-current-step", "0", "--auto-commit"])
    captured = capsys.readouterr()

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1
    assert "not a git working tree" in captured.err


# === Repo-root (_root/) overlay tests ===


def _create_root_overlay(repo: Path, step: int, relpath: str, content: str) -> None:
    """Create ``step_files/<step>/_root/<relpath>`` with ``content``."""

    target = repo / ".workshop" / "step_files" / _step_dir_name(step) / "_root" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_advance_lays_root_overlay_as_sibling_of_travel_assistant(workshop_repo):
    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    (workshop_repo / "travel_assistant" / "main.py").write_text("agent", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2 agent file")
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "description: x")

    result = advance_step.main(["--expected", "1"])

    assert result == 0
    # The _root/ payload lands at the repo root, not inside travel_assistant/.
    assert (workshop_repo / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "description: x"
    assert not (workshop_repo / "travel_assistant" / "travel_toolbox").exists()
    assert not (workshop_repo / "travel_assistant" / "_root").exists()
    # The non-_root step files still overlay into travel_assistant/.
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 2 agent file"
    # Earlier work is preserved (incremental advance).
    assert (workshop_repo / "travel_assistant" / "main.py").read_text(encoding="utf-8") == "agent"


def test_advance_preserves_existing_root_overlay_files(workshop_repo):
    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    # Learner already has a root-level file from earlier work.
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "keep.txt").write_text("earlier", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2")
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "new")

    result = advance_step.main(["--expected", "1"])

    assert result == 0
    assert (workshop_repo / "travel_toolbox" / "keep.txt").read_text(encoding="utf-8") == "earlier"
    assert (workshop_repo / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "new"


def test_advance_backs_up_existing_root_overlay(workshop_repo):
    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("work", encoding="utf-8")
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "toolbox.yaml").write_text("v1", encoding="utf-8")
    # A later step defines the overlay target so it is discovered.
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "v2")
    _create_step_files(workshop_repo, 2, "step 2")

    result = advance_step.main(["--expected", "1"])

    assert result == 0
    assert (
        workshop_repo / ".workshop_instance" / "workshop_backups" / "step-1" / "travel_toolbox" / "toolbox.yaml"
    ).read_text(encoding="utf-8") == "v1"


def test_reset_removes_and_backs_up_root_overlay_target(workshop_repo):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    # Overlay target is discovered from any step_files/*/_root entry.
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "declared")
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "toolbox.yaml").write_text("live", encoding="utf-8")

    result = advance_step.main(["--reset"])

    assert result == 0
    # Reset returns to step 0 and removes the workshop-owned root folder.
    assert not (workshop_repo / "travel_toolbox").exists()
    backups = list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("reset-*"))
    assert backups, "reset should create a timestamped backup"
    assert (backups[0] / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "live"


def test_dry_run_reports_root_overlay_without_writing(workshop_repo, capsys):
    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    _create_step_files(workshop_repo, 2, "step 2")
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "x")

    result = advance_step.main(["--expected", "1", "--dry-run"])
    captured = capsys.readouterr()

    assert result == 0
    assert ".workshop/step_files/02/_root/ onto the repo root" in captured.out
    assert not (workshop_repo / "travel_toolbox").exists()


# === Subprocess-level integration test (proves the documented CLI works) ===


@pytest.fixture
def real_workshop_layout(tmp_path):
    """Synthesize a workshop layout in ``tmp_path`` using the real scripts/docs."""

    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "scripts", tmp_path / ".workshop" / "scripts")
    shutil.copytree(repo_root / "docs", tmp_path / ".workshop" / "docs")
    for step in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 99):
        step_dir = tmp_path / ".workshop" / "step_files" / (str(step) if step == 99 else f"{step:02d}")
        step_dir.mkdir(parents=True)
        (step_dir / "stub.txt").write_text(f"canonical step {step}", encoding="utf-8")
    (tmp_path / "travel_assistant").mkdir()
    (tmp_path / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / ".workshop_instance" / "workshop_backups").mkdir(parents=True)
    (tmp_path / ".workshop_instance" / ".workshop-state.json").write_text(
        json.dumps({"current_step": 0, "schema_version": 1}) + "\n",
        encoding="utf-8",
    )
    # Render the initial README at step 0 using the real renderer so the
    # state-sync check passes when we run advance_step.py as a subprocess.
    subprocess.run(
        [
            sys.executable,
            str(tmp_path / ".workshop" / "scripts" / "render_readme.py"),
            "--step",
            "0",
            "--owner",
            "test-owner",
            "--repo",
            "test-repo",
            "--out",
            str(tmp_path / "README.md"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return tmp_path


@_requires_git
def test_subprocess_advance_with_auto_commit_end_to_end(real_workshop_layout):
    """Run ``python scripts/advance_step.py --expected-current-step N --auto-commit`` as a subprocess."""

    repo = real_workshop_layout
    _git_init_with_identity(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial workshop layout")
    # Sentinel that must NOT be committed by --auto-commit.
    (repo / "untracked_local_edit.txt").write_text("local-only", encoding="utf-8")

    def _advance_subprocess(expected: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(repo / ".workshop" / "scripts" / "advance_step.py"),
                "--expected-current-step",
                expected,
                "--auto-commit",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )

    # 0 -> 1
    result = _advance_subprocess("0")
    assert result.returncode == 0, result.stderr
    assert json.loads(
        (repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1
    log = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: advance to step 1"
    assert "?? untracked_local_edit.txt" in _git(repo, "status", "--porcelain").stdout

    # 1 -> 2 (proves a multi-step local loop works end-to-end)
    result = _advance_subprocess("1")
    assert result.returncode == 0, result.stderr
    assert json.loads(
        (repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 2
    log = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: advance to step 2"
    assert "?? untracked_local_edit.txt" in _git(repo, "status", "--porcelain").stdout


# === --on-push (push-driven auto-advance) tests ===


def test_on_push_advances_one_step_and_reports_advanced(workshop_repo, monkeypatch):
    """A push while mid-workshop advances exactly one step and sets advanced=true."""

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    _create_step_files(workshop_repo, 2, "canonical step 2")
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 2
    assert "# Synthetic step 2" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert "advanced=true\n" in github_output.read_text(encoding="utf-8")


def test_on_push_at_step_zero_is_noop(workshop_repo, monkeypatch, capsys):
    """Setup (step 0) never advances on a push; the manual button owns it."""

    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push"])
    captured = capsys.readouterr()

    assert result == 0
    assert "Start the workshop" in captured.out
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 0
    assert "advanced=false\n" in github_output.read_text(encoding="utf-8")


def test_on_push_at_final_step_is_noop(workshop_repo, monkeypatch, capsys):
    """A push after completion (step 99) is a no-op, not an error."""

    _write_state(workshop_repo, 99)
    _write_readme(workshop_repo, 99)
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push"])
    captured = capsys.readouterr()

    assert result == 0
    assert "already complete" in captured.out.lower()
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 99
    assert "advanced=false\n" in github_output.read_text(encoding="utf-8")


def test_on_push_dry_run_does_not_mutate_state(workshop_repo, monkeypatch):
    """--on-push --dry-run reports the plan without touching state or advanced=true."""

    _write_state(workshop_repo, 3)
    _write_readme(workshop_repo, 3)
    _create_step_files(workshop_repo, 4, "canonical step 4")
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push", "--dry-run"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 3
    assert "advanced=false\n" in github_output.read_text(encoding="utf-8")


def test_on_push_uninitialized_is_noop(workshop_repo, monkeypatch, capsys):
    """No state file and no init marker: a push no-ops rather than racing init."""

    (workshop_repo / ".workshop_instance" / ".workshop-state.json").unlink(missing_ok=True)
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push"])
    captured = capsys.readouterr()

    assert result == 0
    assert "not initialized" in captured.out.lower()
    assert "advanced=false\n" in github_output.read_text(encoding="utf-8")


def test_on_push_missing_state_after_init_fails_loudly(workshop_repo, monkeypatch, capsys):
    """Once the init marker exists, a lost state file is a hard error, not a no-op."""

    (workshop_repo / ".workshop_instance" / ".workshop-state.json").unlink(missing_ok=True)
    marker = workshop_repo / ".workshop_instance" / ".workshop-initialized"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")
    github_output = workshop_repo / "github.output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    result = advance_step.main(["--on-push"])
    captured = capsys.readouterr()

    assert result == 1
    assert "workshop state" in captured.err.lower()
