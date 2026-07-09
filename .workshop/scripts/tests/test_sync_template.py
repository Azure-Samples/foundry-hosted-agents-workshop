"""Tests for ``scripts/sync_template.py``.

The sync script refreshes the workshop machinery (``.workshop/`` and ``.github/``)
from an upstream template without touching participant work or the current step.
These tests pin that contract: it mirrors additions/edits/deletions, it refuses
to overwrite protected paths, it reports a dry run without mutating anything, and
it no-ops when pointed at its own origin.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import scripts.sync_template as sync_template

_requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="requires the `git` binary on PATH",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def upstream_repo(tmp_path: Path) -> Path:
    """An upstream template with newer .workshop/ and .github/ trees."""

    repo = tmp_path / "upstream"
    _init_repo(repo)
    _write(repo, ".workshop/a.txt", "v2")
    _write(repo, ".workshop/new.txt", "brand new")
    _write(repo, ".github/workflows/ci.yml", "name: ci-v2\n")
    # The advance guard must exist upstream and honor the sentinel, or the sync
    # refuses to adopt the .github/ tree (see _verify_upstream_guard). Include
    # the tokens the verifier checks for: the sentinel, proceed=false, grep -qF.
    _write(
        repo,
        ".github/workflows/advance-on-push.yml",
        'name: advance\n'
        'SENTINEL="[skip-advance]"\n'
        'if git log -1 --pretty=%B "$GITHUB_SHA" | grep -qF -- "$SENTINEL"; then\n'
        '  echo "proceed=false" >> "$GITHUB_OUTPUT"\n'
        "fi\n",
    )
    _commit_all(repo, "upstream state")
    return repo


@pytest.fixture
def instance_repo(tmp_path: Path, monkeypatch) -> Path:
    """An instance with older machinery plus participant work to protect."""

    repo = tmp_path / "instance"
    _init_repo(repo)
    _write(repo, ".workshop/a.txt", "v1")
    _write(repo, ".workshop/gone.txt", "removed upstream")
    _write(repo, ".github/workflows/ci.yml", "name: ci-v1\n")
    _write(repo, "travel_assistant/main.py", "my work")
    _write(repo, ".workshop_instance/.workshop-state.json", '{"current_step": 3}\n')
    _write(repo, "README.md", "<!-- step: 3 -->\n")
    _commit_all(repo, "instance state")

    monkeypatch.setattr(sync_template, "REPO_ROOT", repo)
    return repo


@_requires_git
def test_mirror_adds_edits_and_deletes(upstream_repo: Path, instance_repo: Path):
    result = sync_template.sync(upstream_url=str(upstream_repo), ref="main")

    assert result == 0
    # Edited + added upstream files are mirrored.
    assert (instance_repo / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v2"
    assert (instance_repo / ".workshop" / "new.txt").read_text(encoding="utf-8") == "brand new"
    assert (instance_repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == "name: ci-v2\n"
    # File removed upstream is deleted locally...
    assert not (instance_repo / ".workshop" / "gone.txt").exists()
    # ...and the deletion is staged.
    staged = _git(instance_repo, "diff", "--cached", "--name-only").stdout
    assert ".workshop/gone.txt" in staged


@_requires_git
def test_participant_work_and_step_state_untouched(upstream_repo: Path, instance_repo: Path):
    sync_template.sync(upstream_url=str(upstream_repo), ref="main")

    assert (instance_repo / "travel_assistant" / "main.py").read_text(encoding="utf-8") == "my work"
    assert (
        instance_repo / ".workshop_instance" / ".workshop-state.json"
    ).read_text(encoding="utf-8") == '{"current_step": 3}\n'
    assert (instance_repo / "README.md").read_text(encoding="utf-8") == "<!-- step: 3 -->\n"


@_requires_git
def test_commit_carries_skip_advance_sentinel(upstream_repo: Path, instance_repo: Path):
    sync_template.sync(upstream_url=str(upstream_repo), ref="main", commit=True)

    message = _git(instance_repo, "log", "-1", "--pretty=%B").stdout
    assert sync_template.SKIP_ADVANCE_SENTINEL in message


@_requires_git
def test_dry_run_reports_without_mutating(upstream_repo: Path, instance_repo: Path, capsys):
    result = sync_template.sync(upstream_url=str(upstream_repo), ref="main", dry_run=True)
    out = capsys.readouterr().out

    assert result == 0
    assert "DRY RUN" in out
    assert ".workshop/new.txt" in out
    # Working tree is unchanged.
    assert (instance_repo / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v1"
    assert (instance_repo / ".workshop" / "gone.txt").exists()
    assert not _git(instance_repo, "diff", "--cached", "--name-only").stdout.strip()


@_requires_git
def test_self_sync_is_a_noop(upstream_repo: Path, instance_repo: Path):
    _git(instance_repo, "remote", "add", "origin", str(upstream_repo))

    result = sync_template.sync(upstream_url=str(upstream_repo), ref="main")

    assert result == 0
    # Nothing was mirrored because upstream == origin.
    assert (instance_repo / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v1"


def test_validate_paths_rejects_protected_and_escaping():
    for bad in (".workshop_instance", "travel_assistant", "README.md", ".env"):
        with pytest.raises(sync_template.SyncError):
            sync_template._validate_paths([bad])
    with pytest.raises(sync_template.SyncError):
        sync_template._validate_paths(["../outside"])
    with pytest.raises(sync_template.SyncError):
        sync_template._validate_paths([])


def test_validate_paths_accepts_defaults():
    assert sync_template._validate_paths([".workshop", ".github"]) == [".workshop", ".github"]
    assert sync_template._validate_paths([".workshop/"]) == [".workshop"]


def test_validate_paths_rejects_pathspec_and_globs():
    for bad in (":(top)README.md", ":!.workshop", ".workshop/*", "foo?", "a[bc]", "."):
        with pytest.raises(sync_template.SyncError):
            sync_template._validate_paths([bad])


def test_normalize_remote_canonicalizes_equivalent_forms():
    canonical = "github.com/azure-samples/foundry-hosted-agents-workshop"
    forms = [
        "https://github.com/Azure-Samples/foundry-hosted-agents-workshop.git",
        "https://x-access-token:TOKEN@github.com/Azure-Samples/foundry-hosted-agents-workshop.git",
        "git@github.com:Azure-Samples/foundry-hosted-agents-workshop.git",
        "ssh://git@github.com/Azure-Samples/foundry-hosted-agents-workshop",
    ]
    assert {sync_template._normalize_remote(f) for f in forms} == {canonical}


@_requires_git
def test_aborts_when_upstream_lacks_advance_guard(tmp_path: Path, monkeypatch):
    # Upstream with a .github/ tree but NO advance-on-push.yml guard.
    upstream = tmp_path / "no_guard_upstream"
    _init_repo(upstream)
    _write(upstream, ".github/workflows/ci.yml", "name: ci\n")
    _write(upstream, ".workshop/a.txt", "v2")
    _commit_all(upstream, "no guard")

    instance = tmp_path / "instance2"
    _init_repo(instance)
    _write(instance, ".github/workflows/advance-on-push.yml", "honors [skip-advance]\n")
    _write(instance, ".workshop/a.txt", "v1")
    _commit_all(instance, "instance")
    monkeypatch.setattr(sync_template, "REPO_ROOT", instance)

    with pytest.raises(sync_template.SyncError, match="missing"):
        sync_template.sync(upstream_url=str(upstream), ref="main")

    # Nothing was mirrored — the local guard survives untouched.
    assert (instance / ".github" / "workflows" / "advance-on-push.yml").exists()
    assert (instance / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v1"

    # The override lets it proceed.
    assert sync_template.sync(
        upstream_url=str(upstream), ref="main", allow_missing_guard=True
    ) == 0
    assert (instance / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v2"


@_requires_git
def test_aborts_when_upstream_guard_is_comment_only(tmp_path: Path, monkeypatch):
    # Upstream ships an advance-on-push.yml where the sentinel appears only as a
    # comment, with no skip logic. Text presence must NOT satisfy the verifier.
    upstream = tmp_path / "weak_guard_upstream"
    _init_repo(upstream)
    _write(
        upstream,
        ".github/workflows/advance-on-push.yml",
        "name: advance\n# this workflow once honored [skip-advance]\n",
    )
    _write(upstream, ".workshop/a.txt", "v2")
    _commit_all(upstream, "weak guard")

    instance = tmp_path / "instance3"
    _init_repo(instance)
    _write(
        instance,
        ".github/workflows/advance-on-push.yml",
        'SENTINEL="[skip-advance]"\ngrep -qF -- "$SENTINEL" && echo "proceed=false"\n',
    )
    _write(instance, ".workshop/a.txt", "v1")
    _commit_all(instance, "instance")
    monkeypatch.setattr(sync_template, "REPO_ROOT", instance)

    with pytest.raises(sync_template.SyncError, match="honor"):
        sync_template.sync(upstream_url=str(upstream), ref="main")
    assert (instance / ".workshop" / "a.txt").read_text(encoding="utf-8") == "v1"


def test_guard_content_honors_sentinel_heuristic():
    honoring = (
        'SENTINEL="[skip-advance]"\n'
        'if grep -qF -- "$SENTINEL"; then echo "proceed=false"; fi\n'
    )
    assert sync_template._guard_content_honors_sentinel(honoring) is True
    # Sentinel present but only as prose — no skip logic.
    assert sync_template._guard_content_honors_sentinel("# honors [skip-advance]\n") is False
    # Skip logic present but the sentinel was dropped.
    assert sync_template._guard_content_honors_sentinel('grep -qF x\nproceed=false\n') is False


def test_normalized_paths_are_guard_detected():
    for spelling in (".github/.", ".github//workflows", ".github/workflows/."):
        validated = sync_template._validate_paths([spelling])
        assert sync_template._guard_paths_synced(validated), spelling
    assert sync_template._validate_paths([".github//workflows"]) == [".github/workflows"]
    assert sync_template._validate_paths([".github/."]) == [".github"]


def test_git_rejects_non_allowlisted_subcommand():
    for bad in (("reset", "--hard"), ("clean", "-fdx"), ("filter-branch",), ()):
        with pytest.raises(sync_template.SyncError, match="non-allowlisted"):
            sync_template._git(*bad)
