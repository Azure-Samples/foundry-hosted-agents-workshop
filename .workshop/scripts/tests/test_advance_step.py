import json
import io
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
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0" / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "old"
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
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-4" / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "keep me"


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
    assert (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-0" / "travel_assistant" / "leftover.py").exists()


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
    assert (backups[0] / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 5 work"
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "reset starter"
    assert not (workshop_repo / "travel_assistant" / "old.txt").exists()


def test_reset_current_relays_current_step_and_backs_up(workshop_repo, monkeypatch):
    # Learner is on step 5 with local edits. reset-current re-lays the clean
    # step 5 starter set and re-renders step 5's README, but STAYS on step 5.
    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("my edits", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "scratch.txt").write_text("remove me", encoding="utf-8")
    _create_step_files(workshop_repo, 5, "step 5 starter")
    github_env = workshop_repo / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))

    result = advance_step.main(["--reset-current"])

    assert result == 0
    # Still on step 5 — never dropped back to 0.
    assert json.loads((workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text())["current_step"] == 5
    assert "# Synthetic step 5" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    # Learner edits backed up, then replaced by the clean starter; stray file gone.
    backups = list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("reset-current-05-*"))
    assert len(backups) == 1
    assert (backups[0] / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "my edits"
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 5 starter"
    assert not (workshop_repo / "travel_assistant" / "scratch.txt").exists()
    assert github_env.read_text(encoding="utf-8") == "NEW_STEP=5\n"


@_requires_git
def test_reset_current_auto_commit_creates_commit(workshop_repo):
    _write_state(workshop_repo, 4)
    _write_readme(workshop_repo, 4)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 4 work", encoding="utf-8")
    _create_step_files(workshop_repo, 4, "step 4 starter")
    _git_init_with_identity(workshop_repo)

    result = advance_step.main(["--reset-current", "--auto-commit"])

    assert result == 0
    log = _git(workshop_repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: reset current step 4 [skip-advance]"


@_requires_git
def test_reset_current_commit_carries_skip_advance_when_state_unchanged(workshop_repo):
    # reset-current rewrites the state file to the SAME step, so when state is
    # already canonical there is no state-file diff for advance-on-push.yml to key
    # off. The [skip-advance] sentinel is therefore what must keep a pushed
    # reset-current commit from advancing the learner — assert it is always there,
    # even when travel_assistant/ is the only workshop-owned change.
    _write_readme(workshop_repo, 6)
    _create_step_files(workshop_repo, 6, "step 6 starter")
    # Seed the state file in advance_step's own canonical format so a later
    # re-write produces a byte-identical file (no state-file diff).
    advance_step._write_state(6)
    _git_init_with_identity(workshop_repo)
    _git(workshop_repo, "add", "-A")
    _git(workshop_repo, "commit", "-q", "-m", "canonical step 6")
    # Now the learner edits travel_assistant/ only.
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("local edit", encoding="utf-8")
    _git(workshop_repo, "add", "-A")
    _git(workshop_repo, "commit", "-q", "-m", "learner work")

    result = advance_step.main(["--reset-current", "--auto-commit"])

    assert result == 0
    body = _git(workshop_repo, "log", "-1", "--format=%B").stdout
    assert advance_step.SKIP_ADVANCE_SENTINEL in body
    # The state file was byte-identical, so it is NOT part of this commit — proving
    # the sentinel (not a state-file change) is what suppresses the advance.
    changed = _git(workshop_repo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    assert ".workshop_instance/.workshop-state.json" not in changed


def test_reset_current_errors_when_step_files_missing(workshop_repo, capsys):
    # The current step ships no starter files. reset-current must refuse loudly
    # instead of backing up + wiping travel_assistant/ and reporting success with
    # nothing laid back down.
    _write_state(workshop_repo, 3)
    _write_readme(workshop_repo, 3)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("my work", encoding="utf-8")
    # Deliberately do NOT create .workshop/step_files/03/.
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--reset-current"])
    captured = capsys.readouterr()

    assert result == 1
    assert "step_files/03/ is missing" in captured.err
    # Nothing was touched: work preserved, no backup taken, README unchanged.
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "my work"
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert not list((workshop_repo / ".workshop_instance" / "workshop_backups").glob("*"))


def test_reset_and_reset_current_are_mutually_exclusive(workshop_repo, capsys):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)

    with pytest.raises(SystemExit):
        advance_step.main(["--reset", "--reset-current"])
    captured = capsys.readouterr()
    assert "not allowed with argument" in captured.err or "argument --reset" in captured.err


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
        workshop_repo / ".workshop_instance" / "workshop_backups" / "step-1" / "_root" / "travel_toolbox" / "toolbox.yaml"
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
    assert (backups[0] / "_root" / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "live"


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


# === --back (local move-back-one-step) tests ===


def _backups_dir(repo: Path) -> Path:
    return repo / ".workshop_instance" / "workshop_backups"


def _seed_step_backup(repo: Path, step: int, relpath: str, content: str) -> None:
    """Seed the agent snapshot ``step-<step>/travel_assistant/<relpath>`` + manifest.

    Mirrors what advance's atomic snapshot writes: the travel_assistant/ namespace
    plus the completion manifest that marks the backup as restorable.
    """

    target = _backups_dir(repo) / f"step-{step}" / "travel_assistant" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _seed_backup_manifest(repo, step)


def _seed_step_root_backup(repo: Path, step: int, relpath: str, content: str) -> None:
    """Seed a repo-root overlay target ``step-<step>/_root/<relpath>`` + manifest."""

    target = _backups_dir(repo) / f"step-{step}" / "_root" / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _seed_backup_manifest(repo, step)


def _seed_backup_manifest(repo: Path, step: int) -> None:
    """Write the completion manifest that marks ``step-<step>/`` as restorable."""

    manifest = _backups_dir(repo) / f"step-{step}" / "backup.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"format_version": 2, "step": step}, indent=2) + "\n",
        encoding="utf-8",
    )


def test_back_restores_saved_work_from_backup(workshop_repo):
    """Back prefers the step-<prev> snapshot so the learner's real work returns."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    # The snapshot advance took before leaving step 1 (the learner's edited work).
    _seed_step_backup(workshop_repo, 1, "stub.txt", "my edited step 1")
    # Current step-2 workspace: a carried file plus a step-2-only file.
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 2 work", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "step2_only.py").write_text("# step 2\n", encoding="utf-8")

    result = advance_step.main(["--back"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1
    assert "# Synthetic step 1" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    # The learner's saved step-1 file is restored verbatim...
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "my edited step 1"
    # ...and the step-2-only file is gone (it wasn't part of step 1).
    assert not (workshop_repo / "travel_assistant" / "step2_only.py").exists()
    # Current work was snapshotted before the destructive restore.
    back_backups = list(_backups_dir(workshop_repo).glob("back-*"))
    assert len(back_backups) == 1
    assert (back_backups[0] / "travel_assistant" / "step2_only.py").read_text(encoding="utf-8") == "# step 2\n"


def test_back_at_step_zero_errors_without_writes(workshop_repo, capsys):
    _write_state(workshop_repo, 0)
    _write_readme(workshop_repo, 0)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("setup", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 1
    assert "already at step 0" in captured.err
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 0
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "setup"
    assert not list(_backups_dir(workshop_repo).glob("back-*"))


def test_back_from_final_step_returns_to_terminal(workshop_repo):
    """99 -> 9 is the inverse of the cleanup jump and restores the step-9 snapshot."""

    _write_state(workshop_repo, 99)
    _write_readme(workshop_repo, 99)
    _seed_step_backup(workshop_repo, 9, "stub.txt", "step 9 work")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("cleanup", encoding="utf-8")

    result = advance_step.main(["--back"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 9
    assert "# Synthetic step 9" in (workshop_repo / "README.md").read_text(encoding="utf-8")
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 9 work"


def test_back_dry_run_has_no_side_effects(workshop_repo, capsys):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    _seed_step_backup(workshop_repo, 1, "stub.txt", "my edited step 1")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 2 work", encoding="utf-8")
    original_state = (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    original_readme = (workshop_repo / "README.md").read_text(encoding="utf-8")

    result = advance_step.main(["--back", "--dry-run"])
    captured = capsys.readouterr()

    assert result == 0
    assert "DRY RUN: going back step 2 -> 1" in captured.out
    assert "would restore" in captured.out.lower()
    assert (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8") == original_state
    assert (workshop_repo / "README.md").read_text(encoding="utf-8") == original_readme
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 2 work"
    assert not list(_backups_dir(workshop_repo).glob("back-*"))


def test_back_rebuilds_canonically_when_no_backup(workshop_repo, capsys):
    """Without a step-<prev> snapshot, back rebuilds canonical files and warns."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    # No workshop_backups/step-1/ exists. Provide canonical step files for 0..1.
    (workshop_repo / ".workshop" / "step_files" / "00").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "00" / "base0.txt").write_text("b0", encoding="utf-8")
    (workshop_repo / ".workshop" / "step_files" / "01").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "01" / "base1.txt").write_text("b1", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "step2_only.py").write_text("# step 2\n", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 0
    assert "no saved" in captured.err.lower()
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 1
    # Canonical step 0 and step 1 files are laid down...
    assert (workshop_repo / "travel_assistant" / "base0.txt").read_text(encoding="utf-8") == "b0"
    assert (workshop_repo / "travel_assistant" / "base1.txt").read_text(encoding="utf-8") == "b1"
    # ...and the step-2-only file is gone.
    assert not (workshop_repo / "travel_assistant" / "step2_only.py").exists()
    # Current work is still snapshotted before the rebuild.
    back_backups = list(_backups_dir(workshop_repo).glob("back-*"))
    assert len(back_backups) == 1
    assert (back_backups[0] / "travel_assistant" / "step2_only.py").exists()


def test_back_rebuild_missing_step_files_errors_before_clearing(workshop_repo, capsys):
    """A canonical rebuild with a missing step dir must abort before any clear."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    # step_files/00 exists but step_files/01 is intentionally missing, and there
    # is no workshop_backups/step-1/ to restore from.
    (workshop_repo / ".workshop" / "step_files" / "00").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "00" / "base0.txt").write_text("b0", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "work.py").write_text("keep me", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 1
    assert ".workshop/step_files/01/" in captured.err
    # Nothing destructive happened: state, workspace, and backups are untouched.
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 2
    assert (workshop_repo / "travel_assistant" / "work.py").read_text(encoding="utf-8") == "keep me"
    assert not list(_backups_dir(workshop_repo).glob("back-*"))


def test_back_state_desync_exits_nonzero(workshop_repo, capsys):
    """State/README disagreement aborts back before any destructive work."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 3)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("work", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 1
    assert "State desync" in captured.err
    assert not list(_backups_dir(workshop_repo).glob("back-*"))


def test_back_restores_root_overlay_target_to_repo_root(workshop_repo):
    """Restore routes known root-overlay names to the repo root, not travel_assistant/."""

    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    # Declare travel_toolbox as a root overlay target (discovered from any step).
    _create_root_overlay(workshop_repo, 4, "travel_toolbox/toolbox.yaml", "declared")
    # The step-4 snapshot holds both travel_assistant/ content and the root target.
    _seed_step_backup(workshop_repo, 4, "stub.txt", "step 4 work")
    _seed_step_root_backup(workshop_repo, 4, "travel_toolbox/toolbox.yaml", "backed up")
    # Current step-5 state on disk.
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 5 work", encoding="utf-8")
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "toolbox.yaml").write_text("live step 5", encoding="utf-8")

    result = advance_step.main(["--back"])

    assert result == 0
    assert json.loads(
        (workshop_repo / ".workshop_instance" / ".workshop-state.json").read_text(encoding="utf-8")
    )["current_step"] == 4
    # The root target is restored at the repo root...
    assert (workshop_repo / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "backed up"
    # ...and NOT copied inside travel_assistant/.
    assert not (workshop_repo / "travel_assistant" / "travel_toolbox").exists()
    assert (workshop_repo / "travel_assistant" / "stub.txt").read_text(encoding="utf-8") == "step 4 work"
    # The live step-5 root target was snapshotted first.
    back_backups = list(_backups_dir(workshop_repo).glob("back-*"))
    assert (back_backups[0] / "_root" / "travel_toolbox" / "toolbox.yaml").read_text(encoding="utf-8") == "live step 5"


@_requires_git
def test_back_auto_commit_creates_go_back_commit(workshop_repo):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    _seed_step_backup(workshop_repo, 1, "stub.txt", "my edited step 1")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 2 work", encoding="utf-8")
    _git_init_with_identity(workshop_repo)

    result = advance_step.main(["--back", "--auto-commit"])

    assert result == 0
    log = _git(workshop_repo, "log", "-1", "--format=%s").stdout.strip()
    assert log == "workshop: go back to step 1"


def test_back_and_reset_are_mutually_exclusive(workshop_repo, capsys):
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)

    with pytest.raises(SystemExit):
        advance_step.main(["--back", "--reset"])
    captured = capsys.readouterr()
    assert "not allowed with argument" in captured.err or "argument" in captured.err


def test_back_backups_do_not_merge_within_same_second(workshop_repo, monkeypatch):
    """Two backs in the same UTC second must land in distinct backup dirs.

    ``_reserve_backup_dir`` uses an exclusive mkdir and a ``-N`` suffix so a
    rapid back-then-back never merges the two snapshots into one directory,
    keeping the "current work is always backed up first" guarantee true.
    """

    class _FrozenDatetime(advance_step.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 9, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(advance_step, "datetime", _FrozenDatetime)

    # 3 -> 2 (backs up "step 3 work"), then 2 -> 1 (backs up restored "step 2 saved").
    _write_state(workshop_repo, 3)
    _write_readme(workshop_repo, 3)
    _seed_step_backup(workshop_repo, 2, "stub.txt", "step 2 saved")
    _seed_step_backup(workshop_repo, 1, "stub.txt", "step 1 saved")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 3 work", encoding="utf-8")

    assert advance_step.main(["--back"]) == 0
    assert advance_step.main(["--back"]) == 0

    back_dirs = sorted(p.name for p in _backups_dir(workshop_repo).glob("back-*"))
    assert len(back_dirs) == 2, f"expected two distinct back dirs, got {back_dirs}"
    contents = {
        (p / "travel_assistant" / "stub.txt").read_text(encoding="utf-8")
        for p in _backups_dir(workshop_repo).glob("back-*")
    }
    assert contents == {"step 3 work", "step 2 saved"}


def test_back_does_not_misroute_agent_file_named_after_root_target(workshop_repo):
    """#1: a travel_assistant/ entry sharing a root-target name round-trips back
    into travel_assistant/, never the repo root.

    The namespaced backup layout keeps the agent snapshot under
    ``travel_assistant/`` and root targets under ``_root/``, so restore is
    structural and cannot misroute a learner dir that happens to be named after
    a root overlay target.
    """

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    # travel_toolbox is a declared root overlay target...
    _create_root_overlay(workshop_repo, 2, "travel_toolbox/toolbox.yaml", "declared")
    _create_step_files(workshop_repo, 2, "step 2 file")
    # ...but the learner also has a DIRECTORY named travel_toolbox INSIDE
    # travel_assistant/ (the collision case).
    inner = workshop_repo / "travel_assistant" / "travel_toolbox"
    inner.mkdir()
    (inner / "notes.md").write_text("my agent notes", encoding="utf-8")

    assert advance_step.main(["--expected", "1"]) == 0  # snapshots step-1
    assert advance_step.main(["--back"]) == 0           # restores step-1

    # The learner's file returns INSIDE travel_assistant/, not at the repo root.
    assert (workshop_repo / "travel_assistant" / "travel_toolbox" / "notes.md").read_text(
        encoding="utf-8"
    ) == "my agent notes"
    assert not (workshop_repo / "travel_toolbox" / "notes.md").exists()


def test_back_after_revisit_does_not_resurrect_deleted_file(workshop_repo):
    """#3: re-advancing a revisited step REPLACES its backup, so a file deleted
    on the revisit is not resurrected by a later back."""

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    (workshop_repo / "travel_assistant" / "keep.txt").write_text("keep", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "doomed.txt").write_text("delete me later", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2 file")

    assert advance_step.main(["--expected", "1"]) == 0  # 1 -> 2, snapshots step-1
    assert advance_step.main(["--back"]) == 0           # 2 -> 1, restores both files
    assert (workshop_repo / "travel_assistant" / "doomed.txt").exists()

    # On the revisit the learner deletes doomed.txt, then re-advances.
    (workshop_repo / "travel_assistant" / "doomed.txt").unlink()
    assert advance_step.main(["--expected", "1"]) == 0  # 1 -> 2, REPLACES step-1

    assert advance_step.main(["--back"]) == 0           # 2 -> 1 again
    assert (workshop_repo / "travel_assistant" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (workshop_repo / "travel_assistant" / "doomed.txt").exists()


def test_advance_drops_stale_backup_when_workspace_emptied(workshop_repo):
    """#3 (empty case): emptying a revisited workspace before re-advancing drops
    the stale snapshot so a later back never restores removed files."""

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    _seed_step_backup(workshop_repo, 1, "old.txt", "stale")
    # The learner emptied travel_assistant/ down to the placeholder.
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2 file")

    assert advance_step.main(["--expected", "1"]) == 0

    # Nothing worth backing up -> the stale step-1 snapshot is gone.
    assert not (workshop_repo / ".workshop_instance" / "workshop_backups" / "step-1").exists()


def test_back_treats_legacy_flattened_backup_as_unrestorable(workshop_repo, capsys):
    """A legacy step-<prev> backup without a manifest is not restored; back falls
    back to a canonical rebuild and says the backup is legacy."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    # Legacy flattened backup: files directly under step-1/, no manifest, no namespaces.
    legacy = _backups_dir(workshop_repo) / "step-1"
    legacy.mkdir(parents=True)
    (legacy / "stub.txt").write_text("legacy work", encoding="utf-8")
    # Canonical step files for the rebuild fallback.
    (workshop_repo / ".workshop" / "step_files" / "00").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "00" / "base0.txt").write_text("b0", encoding="utf-8")
    (workshop_repo / ".workshop" / "step_files" / "01").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "01" / "base1.txt").write_text("b1", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "step2_only.py").write_text("# step 2\n", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 0
    assert "legacy" in captured.err.lower()
    # Canonical rebuild happened; the legacy content was NOT restored.
    assert (workshop_repo / "travel_assistant" / "base1.txt").read_text(encoding="utf-8") == "b1"
    assert not (workshop_repo / "travel_assistant" / "stub.txt").exists()


def test_back_restores_root_target_that_is_a_file(workshop_repo):
    """A root overlay target that is a FILE (not a dir) restores from _root/."""

    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    _create_root_overlay(workshop_repo, 4, "toolbox.yaml", "declared")  # a file target
    _seed_step_backup(workshop_repo, 4, "stub.txt", "step 4 work")
    _seed_step_root_backup(workshop_repo, 4, "toolbox.yaml", "backed up file")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 5 work", encoding="utf-8")
    (workshop_repo / "toolbox.yaml").write_text("live", encoding="utf-8")

    assert advance_step.main(["--back"]) == 0

    assert (workshop_repo / "toolbox.yaml").read_text(encoding="utf-8") == "backed up file"
    assert not (workshop_repo / "travel_assistant" / "toolbox.yaml").exists()


def test_snapshot_publish_failure_preserves_previous_backup(workshop_repo, monkeypatch):
    """#1: if the atomic publish swap fails, the prior step backup is rolled back
    and no staging/superseded orphans are left behind.

    ``_snapshot_current_step`` renames the old snapshot aside, swaps the new one
    into place, and only then deletes the old copy. A failure during the swap must
    restore the old snapshot so ``--back`` always has a valid backup to restore.
    """

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    _seed_step_backup(workshop_repo, 1, "stub.txt", "old backup")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("current work", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2 file")

    real_replace = advance_step.os.replace

    def failing_replace(src, dst, *args, **kwargs):
        # Fail only on the publish swap (staging -> final); the rename-aside and
        # rollback operate on step-1 / step-1.superseded, which do not match.
        if ".staging-" in str(src):
            raise OSError("simulated publish failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(advance_step.os, "replace", failing_replace)

    assert advance_step.main(["--expected", "1"]) != 0

    backups = _backups_dir(workshop_repo)
    # The previous backup is intact (rolled back), not lost or half-written.
    assert (backups / "step-1" / "travel_assistant" / "stub.txt").read_text(
        encoding="utf-8"
    ) == "old backup"
    # No staging or superseded directories leaked.
    assert not list(backups.glob("*.staging*"))
    assert not list(backups.glob("*.superseded*"))


def test_snapshot_always_captures_agent_namespace_for_root_only_work(workshop_repo):
    """#2: a root-only snapshot still records travel_assistant/ (even a lone
    .gitkeep) so a later --back restores the placeholder instead of an empty dir.
    """

    _write_state(workshop_repo, 1)
    _write_readme(workshop_repo, 1)
    # Agent workspace is just the placeholder; the real work this step is root-level.
    (workshop_repo / "travel_assistant" / ".gitkeep").write_text("", encoding="utf-8")
    _create_root_overlay(workshop_repo, 1, "travel_toolbox/tb.yaml", "declared")
    (workshop_repo / "travel_toolbox").mkdir()
    (workshop_repo / "travel_toolbox" / "tb.yaml").write_text("root work", encoding="utf-8")
    _create_step_files(workshop_repo, 2, "step 2 file")

    assert advance_step.main(["--expected", "1"]) == 0

    # The agent namespace is captured despite holding only the placeholder.
    assert (
        _backups_dir(workshop_repo) / "step-1" / "travel_assistant" / ".gitkeep"
    ).is_file()

    assert advance_step.main(["--back"]) == 0
    # Restore reproduces travel_assistant/ exactly: placeholder back, step-2 file gone.
    assert (workshop_repo / "travel_assistant" / ".gitkeep").is_file()
    assert not (workshop_repo / "travel_assistant" / "stub.txt").exists()


@pytest.mark.parametrize("relpath", [".github/workflows/ci.yml", "Makefile", ".GITHUB/ci.yml"])
def test_root_overlay_rejects_machinery_target_name(workshop_repo, relpath):
    """#3: a _root overlay that targets workshop machinery (or a case variant of
    it) is rejected before any backup/clear/restore can corrupt the repo."""

    _create_root_overlay(workshop_repo, 2, relpath, "x")

    with pytest.raises(advance_step.AdvanceError, match="protected repo path"):
        advance_step._root_overlay_targets()


def test_back_replaces_stale_undeclared_root_target(workshop_repo):
    """#4: restore is an exact replacement even for a backup target the current
    step files no longer declare (so the caller's clear pass never touched it)."""

    _write_state(workshop_repo, 5)
    _write_readme(workshop_repo, 5)
    # travel_toolbox is captured in the step-4 backup but NOT declared by any
    # current step_files/_root, so _clear_root_targets leaves the live copy alone.
    _seed_step_backup(workshop_repo, 4, "stub.txt", "step 4 work")
    _seed_step_root_backup(workshop_repo, 4, "travel_toolbox/keep.txt", "kept")
    live = workshop_repo / "travel_toolbox"
    live.mkdir()
    (live / "keep.txt").write_text("stale", encoding="utf-8")
    (live / "extra.txt").write_text("should be gone after restore", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("step 5 work", encoding="utf-8")

    assert advance_step.main(["--back"]) == 0

    assert (live / "keep.txt").read_text(encoding="utf-8") == "kept"
    # The stale, undeclared extra file is replaced away, not merged over.
    assert not (live / "extra.txt").exists()


def test_back_treats_malformed_manifest_as_unrestorable(workshop_repo, capsys):
    """#5: a manifest whose step is not an integer is rejected as malformed, so
    --back falls back to a canonical rebuild rather than trusting it."""

    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    backup = _backups_dir(workshop_repo) / "step-1"
    (backup / "travel_assistant").mkdir(parents=True)
    (backup / "travel_assistant" / "forged.txt").write_text("do not restore", encoding="utf-8")
    (backup / "backup.json").write_text(
        json.dumps({"format_version": 2, "step": "oops"}) + "\n", encoding="utf-8"
    )
    # Canonical step files for the rebuild fallback.
    (workshop_repo / ".workshop" / "step_files" / "00").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "00" / "base0.txt").write_text("b0", encoding="utf-8")
    (workshop_repo / ".workshop" / "step_files" / "01").mkdir(parents=True)
    (workshop_repo / ".workshop" / "step_files" / "01" / "base1.txt").write_text("b1", encoding="utf-8")
    (workshop_repo / "travel_assistant" / "step2_only.py").write_text("# step 2\n", encoding="utf-8")

    result = advance_step.main(["--back"])
    captured = capsys.readouterr()

    assert result == 0
    assert "legacy" in captured.err.lower()
    # Canonical rebuild happened; the forged backup content was NOT restored.
    assert (workshop_repo / "travel_assistant" / "base1.txt").read_text(encoding="utf-8") == "b1"
    assert not (workshop_repo / "travel_assistant" / "forged.txt").exists()


def test_root_overlay_rejects_protected_target_name(workshop_repo):
    """A _root overlay that targets a protected repo path is rejected up front."""

    _create_root_overlay(workshop_repo, 2, "travel_assistant/oops.txt", "x")

    with pytest.raises(advance_step.AdvanceError, match="protected repo path"):
        advance_step._root_overlay_targets()


def test_reset_backups_are_unique_within_same_second(workshop_repo, monkeypatch):
    """Two resets in the same UTC second land in distinct backup dirs."""

    class _FrozenDatetime(advance_step.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 9, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(advance_step, "datetime", _FrozenDatetime)

    _create_step_files(workshop_repo, 0, "starter")
    _write_state(workshop_repo, 2)
    _write_readme(workshop_repo, 2)
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("work a", encoding="utf-8")

    assert advance_step.main(["--reset"]) == 0
    (workshop_repo / "travel_assistant" / "stub.txt").write_text("work b", encoding="utf-8")
    assert advance_step.main(["--reset"]) == 0

    reset_dirs = sorted(p.name for p in _backups_dir(workshop_repo).glob("reset-*"))
    assert len(reset_dirs) == 2, f"expected two distinct reset dirs, got {reset_dirs}"


# --- machinery-only push classification (advance-on-push guard #4) -----------


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/advance-on-push.yml",
        ".workshop/scripts/sync_template.py",
        ".workshop_instance/workshop_backups/x",
        ".devcontainer/devcontainer.json",
        ".vscode/settings.json",
        "Makefile",
        "README.md",
        ".env.example",
        ".gitignore",
        "CONTRIBUTING.md",
        ".github",
    ],
)
def test_is_machinery_path_true_for_machinery(path):
    assert advance_step._is_machinery_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "travel_assistant/main.py",
        "travel_assistant",
        "travel_toolbox/toolbox.yaml",
        "travel_indexer/provision_index.py",
        "foundry_skills/skills/x/SKILL.md",
        ".github-notes/readme",  # near-miss: not under .github/
        ".workshopped/file",  # near-miss: not under .workshop/
        "",
    ],
)
def test_is_machinery_path_false_for_delivery_and_near_misses(path):
    assert advance_step._is_machinery_path(path) is False


def test_is_machinery_only_push_true_when_all_machinery():
    changed = [".github/workflows/ci.yml", "Makefile", ".workshop/scripts/x.py"]
    assert advance_step._is_machinery_only_push(changed) is True


def test_is_machinery_only_push_false_when_any_delivery():
    changed = ["Makefile", "travel_assistant/main.py"]
    assert advance_step._is_machinery_only_push(changed) is False


def test_is_machinery_only_push_false_for_root_overlay_delivery():
    """A push that only touches a _root overlay sibling is real progress."""

    assert advance_step._is_machinery_only_push(["travel_toolbox/toolbox.yaml"]) is False


def test_is_machinery_only_push_false_when_empty():
    """No changed paths => no basis to suppress an advance."""

    assert advance_step._is_machinery_only_push([]) is False
    assert advance_step._is_machinery_only_push(["", "  ", "\n"]) is False


def test_check_machinery_only_cli_prints_true(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(".github/workflows/ci.yml\nMakefile\n.workshop/x.py\n"),
    )

    result = advance_step.main(["--check-machinery-only"])

    assert result == 0
    assert capsys.readouterr().out.strip() == "true"


def test_check_machinery_only_cli_prints_false_for_mixed(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("Makefile\ntravel_assistant/main.py\n"),
    )

    result = advance_step.main(["--check-machinery-only"])

    assert result == 0
    assert capsys.readouterr().out.strip() == "false"


def test_machinery_paths_cover_every_tracked_root_entry():
    """Guard MACHINERY_PATHS against drifting behind new root-level template files.

    Every tracked top-level path in the *real* repo must classify as either
    machinery (``MACHINERY_PATHS``) or known delivery (``travel_assistant/`` or a
    ``_root`` overlay sibling like ``travel_toolbox/``). If upstream later adds a
    new root-level machinery file (e.g. ``.editorconfig``) and nobody adds it to
    ``MACHINERY_PATHS``, a participant who manually pulls and pushes only that
    file would be wrongly advanced — reintroducing the exact bug this guard
    fixes. This test fails loudly in that case, forcing the maintainer to
    classify the new path.

    Runs against the real repository root (this test intentionally does NOT use
    the ``workshop_repo`` fixture, so ``advance_step.REPO_ROOT`` is unpatched).
    """

    repo_root = advance_step.REPO_ROOT
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    top_level = {line.split("/", 1)[0] for line in tracked if line}

    delivery = {advance_step.TRAVEL_ASSISTANT_DIR} | advance_step._root_overlay_targets()
    unclassified = sorted(
        name
        for name in top_level
        if not advance_step._is_machinery_path(name) and name not in delivery
    )

    assert not unclassified, (
        f"Unclassified top-level path(s): {unclassified}. Add each to "
        "MACHINERY_PATHS in advance_step.py if it is workshop machinery/platform "
        "scaffolding, or confirm it is participant delivery."
    )
