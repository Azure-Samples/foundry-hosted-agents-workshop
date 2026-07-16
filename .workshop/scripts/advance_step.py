"""Advance or reset the workshop repository state.

This CLI powers both the GitHub Actions workflows and the fully-local
workshop flow. It validates the committed workshop state, backs up
participant work, lays down canonical step files, renders the README, and
updates the state file. Pass ``--auto-commit`` to also stage and commit the
workshop-owned paths when running locally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).parent))
from render_readme import FINAL_STEP, STEP_TITLES, TERMINAL_STEP, parse_step_marker, render

# The workshop authoring material lives under .workshop/ and this script sits at
# .workshop/scripts/advance_step.py, so the repository root is two levels up.
# All path constants below are resolved relative to REPO_ROOT via _path():
#   - authoring material (step_files) lives under .workshop/
#   - per-instance runtime state (state file, backups, init marker) lives under
#     .workshop_instance/
#   - the delivery (travel_assistant/, README.md) stays at the root
REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ".workshop_instance/.workshop-state.json"
INIT_MARKER = ".workshop_instance/.workshop-initialized"
README_FILE = "README.md"
TRAVEL_ASSISTANT_DIR = "travel_assistant"
STEP_FILES_DIR = ".workshop/step_files"
# Reserved subfolder inside .workshop/step_files/<step>/. Its contents are laid down
# at the repository root (as siblings of travel_assistant/) instead of inside
# travel_assistant/. This lets a step ship files that must live outside the agent
# snapshot — e.g. travel_toolbox/toolbox.yaml, which azd ai agent init must not
# copy into the deployed agent.
ROOT_OVERLAY_DIR = "_root"
BACKUPS_DIR = ".workshop_instance/workshop_backups"
# Backups are namespaced so the agent snapshot and the repo-root overlay targets
# never collide at the backup's top level: travel_assistant/ contents go under
# <backup>/travel_assistant/ and repo-root overlay targets under <backup>/_root/.
# The subdir names deliberately mirror the source layout. A completion manifest
# (BACKUP_MANIFEST, written last) marks a snapshot as a valid, fully-written
# new-format backup; --back only restores snapshots that carry it, so a partial
# write or an older flattened backup is never mistaken for restorable work.
BACKUP_AGENT_SUBDIR = TRAVEL_ASSISTANT_DIR
BACKUP_ROOT_SUBDIR = ROOT_OVERLAY_DIR
BACKUP_MANIFEST = "backup.json"
BACKUP_FORMAT_VERSION = 2
# Repo-root paths a _root overlay target must never shadow — backing up, clearing,
# or restoring such a name would corrupt the repository or the backup store itself
# (e.g. a target named ".workshop_instance" would delete the backups). Defined just
# below MACHINERY_PATHS, which it reuses as the single source of truth.
SCHEMA_VERSION = 1
# Commit-message sentinel that advance-on-push.yml looks for to suppress an
# auto-advance. reset-current re-lays the CURRENT step (it must not move to the
# next one), and it rewrites the state file to the *same* step — often a no-op
# diff — so the state-file guard in advance-on-push.yml cannot be relied on to
# catch it. The sentinel is the explicit, robust signal that a push must not
# advance. Keep this literal in sync with sync_template.SKIP_ADVANCE_SENTINEL and
# the SENTINEL in .github/workflows/advance-on-push.yml.
SKIP_ADVANCE_SENTINEL = "[skip-advance]"
# Repo paths that are workshop machinery or platform scaffolding — never the
# participant's delivery (travel_assistant/ and the _root overlay siblings such
# as travel_toolbox/, travel_indexer/, foundry_skills/). A push that changes ONLY
# these paths is not step progress, so advance-on-push.yml must not advance the
# workshop on it. This lets a participant pull the latest machinery (.github/,
# .workshop/, Makefile, …) and push it — even MANUALLY, without the
# [skip-advance] sentinel the sync tooling adds — without being bumped to the
# next step. This is the single source of truth: advance-on-push.yml classifies a
# push by piping its changed paths to `advance_step.py --check-machinery-only`, so
# the list is never duplicated in the workflow.
MACHINERY_PATHS = (
    ".github",
    ".workshop",
    ".workshop_instance",
    ".devcontainer",
    ".vscode",
    "Makefile",
    "README.md",
    ".env.example",
    ".gitignore",
    ".gitattributes",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "LICENSE",
)
# Repo-root names a _root overlay target must never shadow. A target that resolves
# to workshop machinery/platform scaffolding (MACHINERY_PATHS) or to the Git
# metadata directory would let reset/back back up, clear, or restore over the
# repository's own infrastructure — so those names are rejected up front. Stored
# casefolded and compared casefolded so a case variant (e.g. ".GIT" on a
# case-insensitive filesystem) cannot slip past the guard.
_PROTECTED_ROOT_TARGETS = frozenset(
    name.casefold() for name in (*MACHINERY_PATHS, TRAVEL_ASSISTANT_DIR, ".git")
)
# Paths that --auto-commit is allowed to stage. Limited to workshop-owned
# locations so unrelated local edits, untracked files, or secrets are never
# swept into a commit by accident.
_AUTO_COMMIT_PATHS = (
    STATE_FILE,
    README_FILE,
    TRAVEL_ASSISTANT_DIR,
    BACKUPS_DIR,
)
_PLACEHOLDER_OWNER = "{{OWNER}}"
_PLACEHOLDER_REPO = "{{REPO}}"
_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


class AdvanceError(RuntimeError):
    """Raised when the workshop cannot be advanced safely."""


def _is_machinery_path(path: str) -> bool:
    """Return True when ``path`` is workshop machinery/platform, not delivery.

    A path matches when it equals a ``MACHINERY_PATHS`` entry or sits under one
    (prefix + "/"). Leading/trailing slashes and whitespace are ignored so the
    check is robust to however the caller formats the path.
    """

    normalized = path.strip().strip("/")
    if not normalized:
        return False
    return any(
        normalized == entry or normalized.startswith(f"{entry}/")
        for entry in MACHINERY_PATHS
    )


def _is_machinery_only_push(paths: Sequence[str]) -> bool:
    """Return True when a non-empty set of changed ``paths`` is *all* machinery.

    An empty set is not machinery-only: with nothing to classify there is no
    basis to suppress an advance, so the caller falls through to its normal
    behavior.
    """

    changed = [p for p in (path.strip() for path in paths) if p]
    return bool(changed) and all(_is_machinery_path(p) for p in changed)


def _run_check_machinery_only() -> int:
    """Print ``true``/``false`` for the newline-separated paths read from stdin.

    Used by ``.github/workflows/advance-on-push.yml`` to decide whether a push
    touched only workshop machinery (and therefore must not advance the step).
    """

    paths = sys.stdin.read().splitlines()
    print("true" if _is_machinery_only_push(paths) else "false")
    return 0


def _step_dir_name(step: int) -> str:
    """Return the step_files directory name for a workshop step."""

    return str(step) if step == FINAL_STEP else f"{step:02d}"


def _path(relative_path: str) -> Path:
    """Resolve a repository-relative path."""

    return REPO_ROOT / relative_path


def _load_state() -> int:
    """Load and validate the current step from ``.workshop-state.json``."""

    state_path = _path(STATE_FILE)
    try:
        raw_state = state_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AdvanceError(f"Missing {STATE_FILE}; cannot determine workshop state.") from exc
    except OSError as exc:
        raise AdvanceError(f"Failed to read {STATE_FILE}: {exc}") from exc

    try:
        state: Any = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise AdvanceError(f"Malformed {STATE_FILE}: {exc}") from exc

    if not isinstance(state, dict):
        raise AdvanceError(f"Malformed {STATE_FILE}: expected a JSON object.")

    current_step = state.get("current_step")
    schema_version = state.get("schema_version")
    if type(current_step) is not int:
        raise AdvanceError(f"Malformed {STATE_FILE}: current_step must be an integer.")
    if current_step not in set(range(0, TERMINAL_STEP + 1)) | {FINAL_STEP}:
        raise AdvanceError(f"Malformed {STATE_FILE}: unsupported current_step {current_step}.")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise AdvanceError(
            f"Malformed {STATE_FILE}: schema_version must be {SCHEMA_VERSION}."
        )
    return current_step


def _read_readme_marker() -> int | None:
    """Read ``README.md`` and return its step marker, if present."""

    readme_path = _path(README_FILE)
    try:
        readme_text = readme_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AdvanceError(f"Missing {README_FILE}; cannot validate workshop state.") from exc
    except OSError as exc:
        raise AdvanceError(f"Failed to read {README_FILE}: {exc}") from exc
    return parse_step_marker(readme_text)


def _validate_state_sync(current_step: int, readme_step: int | None) -> None:
    """Fail when state and README step marker disagree."""

    if readme_step is not None and readme_step != current_step:
        raise AdvanceError(
            "State desync: .workshop-state.json says step "
            f"{current_step} but README.md says step {readme_step}. Repair manually."
        )


def _compute_next_step(current_step: int) -> int:
    """Return the next workshop step, including the final cleanup jump."""

    if current_step == FINAL_STEP:
        raise AdvanceError("Workshop already complete. Use Reset workshop to restart.")
    if current_step == TERMINAL_STEP:
        return FINAL_STEP
    return current_step + 1


def _compute_previous_step(current_step: int) -> int:
    """Return the previous workshop step, inverting the final cleanup jump."""

    if current_step == 0:
        raise AdvanceError("Workshop is already at step 0; there is no previous step.")
    if current_step == FINAL_STEP:
        return TERMINAL_STEP
    return current_step - 1


def _validate_expected(expected: str | None, current_step: int) -> None:
    """Validate the workflow's expected current step guard.

    ``expected is None`` means the caller (typically a local user running the
    script directly) opted out of the guard; print a notice and continue. An
    explicit empty string is still rejected so the workflow can't silently
    skip the check when its input is misconfigured.
    """

    if expected is None:
        print(
            f"No --expected guard provided; advancing from current step {current_step}."
        )
        return
    try:
        expected_step = int(expected)
    except ValueError as exc:
        raise AdvanceError(f"--expected must be an integer, got {expected!r}.") from exc
    if expected_step != current_step:
        raise AdvanceError(
            f"Expected step {expected_step} but the repo is on step {current_step}. "
            "Pull, refresh, and click the latest README's button."
        )


def _has_backup_worthy_contents(directory: Path) -> bool:
    """Return True when ``directory`` has content other than a lone ``.gitkeep``."""

    try:
        entries = list(directory.rglob("*"))
    except OSError as exc:
        raise AdvanceError(f"Failed to inspect {directory}: {exc}") from exc

    if not entries:
        return False
    relative_names = {entry.relative_to(directory).as_posix() for entry in entries}
    return relative_names != {".gitkeep"}


def _copy_tree(source: Path, destination: Path, *, ignore=None) -> None:
    """Copy ``source`` over ``destination`` with clear filesystem errors.

    ``ignore`` is forwarded to :func:`shutil.copytree` (e.g.
    ``shutil.ignore_patterns(ROOT_OVERLAY_DIR)`` to skip the repo-root overlay
    folder when laying files into ``travel_assistant/``).
    """

    try:
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)
    except OSError as exc:
        raise AdvanceError(f"Failed to copy {source} to {destination}: {exc}") from exc


def _replace_path_with(source: Path | None, destination: Path) -> None:
    """Clear ``destination`` then copy ``source`` onto it (exact replacement).

    Removes whatever is at ``destination`` first — a directory, a file, or a
    symlink — so the copy never merges into leftover contents. When ``source`` is
    ``None`` the destination is only cleared. Used by restore so a snapshot is
    reproduced exactly even over a stale target the caller did not clear.
    """

    try:
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        elif destination.exists() or destination.is_symlink():
            destination.unlink()
    except OSError as exc:
        raise AdvanceError(f"Failed to clear {destination}: {exc}") from exc
    if source is None:
        return
    if source.is_dir():
        _copy_tree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise AdvanceError(f"Failed to copy {source} to {destination}: {exc}") from exc


def _root_overlay_targets() -> set[str]:
    """Return the top-level repo-root paths the workshop owns via ``_root`` overlays.

    Scans every ``.workshop/step_files/<step>/_root/`` directory and collects the
    names of its top-level entries (e.g. ``travel_toolbox``). These are delivered as
    siblings of ``travel_assistant/`` so init/reset can clean them and backups can
    capture them without hardcoding any specific folder name.
    """

    targets: set[str] = set()
    step_files_root = _path(STEP_FILES_DIR)
    if not step_files_root.is_dir():
        return targets
    for step_dir in step_files_root.iterdir():
        overlay = step_dir / ROOT_OVERLAY_DIR
        if not overlay.is_dir():
            continue
        for entry in overlay.iterdir():
            if entry.name.casefold() in _PROTECTED_ROOT_TARGETS:
                raise AdvanceError(
                    f".workshop/step_files/{step_dir.name}/{ROOT_OVERLAY_DIR}/{entry.name} "
                    f"targets a protected repo path ({entry.name}); rename the overlay entry."
                )
            targets.add(entry.name)
    return targets


def _overlay_root_files(step: int) -> None:
    """Overlay ``.workshop/step_files/<step>/_root/`` onto the repo root, if present.

    Contents are copied as siblings of ``travel_assistant/`` without clearing, so
    a learner's earlier root-level work is preserved (advance is incremental).
    """

    overlay = _path(STEP_FILES_DIR) / _step_dir_name(step) / ROOT_OVERLAY_DIR
    if overlay.is_dir():
        _copy_tree(overlay, REPO_ROOT)


def _clear_root_targets() -> None:
    """Remove workshop-owned repo-root overlay targets (used by init/reset)."""

    for name in _root_overlay_targets():
        target = _path(name)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError as exc:
            raise AdvanceError(f"Failed to clear {target}: {exc}") from exc


def _backup_root_targets(destination: Path) -> bool:
    """Back up existing repo-root overlay targets under ``destination/_root/``.

    Returns True when at least one target was backed up so callers can report it.
    """

    backed_up = False
    root_dest = destination / BACKUP_ROOT_SUBDIR
    for name in _root_overlay_targets():
        source = _path(name)
        if not source.exists():
            continue
        dest = root_dest / name
        if source.is_dir():
            _copy_tree(source, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, dest)
            except OSError as exc:
                raise AdvanceError(f"Failed to copy {source} to {dest}: {exc}") from exc
        backed_up = True
    return backed_up


def _backup_travel_assistant(destination: Path) -> bool:
    """Back up ``travel_assistant`` under ``destination/travel_assistant/``.

    Always copies (an empty snapshot is still a valid snapshot). Returns True when
    the source had backup-worthy contents so callers can decide whether to report
    it — the copy itself is unconditional.
    """

    source = _path(TRAVEL_ASSISTANT_DIR)
    try:
        source.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdvanceError(f"Failed to create {source}: {exc}") from exc

    _copy_tree(source, destination / BACKUP_AGENT_SUBDIR)
    return _has_backup_worthy_contents(source)


def _write_backup_manifest(destination: Path, step: int) -> None:
    """Write the completion manifest that marks ``destination`` as a valid snapshot.

    Written last so a snapshot is only ever considered restorable once both
    namespaces have been copied in full (see :func:`_is_restorable_backup`).
    """

    payload = {"format_version": BACKUP_FORMAT_VERSION, "step": step}
    _write_text(destination / BACKUP_MANIFEST, json.dumps(payload, indent=2) + "\n")


def _is_restorable_backup(backup_dir: Path) -> bool:
    """Return True when ``backup_dir`` is a complete, new-format snapshot.

    Restoration is gated on a valid completion manifest rather than on directory
    contents, so an exactly-empty snapshot is still restored verbatim and a
    partially-written or legacy flattened backup is never misread as restorable.
    """

    manifest = backup_dir / BACKUP_MANIFEST
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("format_version") == BACKUP_FORMAT_VERSION
        # A well-formed v2 manifest always records an integer step (bool is
        # rejected explicitly); anything else is a malformed or forged manifest.
        and isinstance(data.get("step"), int)
        and not isinstance(data.get("step"), bool)
    )


def _snapshot_current_step(step: int) -> Path | None:
    """Atomically (re)create ``workshop_backups/step-<step>/`` for the current work.

    Builds the snapshot in a staging directory, always copies ``travel_assistant/``
    (even when it only holds a ``.gitkeep`` placeholder) plus any root overlay
    targets into their namespaces, writes the completion manifest last, then swaps
    it into place. Any prior ``step-<step>/`` snapshot is fully **replaced** — never
    merged — so files the learner deleted on a revisit are not resurrected by a
    later ``--back``.

    Publication is crash-safe: the prior snapshot is renamed aside first, the new
    snapshot is renamed into place, and only then is the old copy deleted. If the
    swap fails, the prior snapshot is rolled back, so there is never a moment where
    the step has no valid backup. The staging directory is removed on every failure
    path.

    Returns the published path, or ``None`` when there was nothing worth backing
    up (empty ``travel_assistant/`` and no root targets), in which case any stale
    snapshot is dropped so ``--back`` reflects the emptied workspace.
    """

    final = _path(BACKUPS_DIR) / f"step-{step}"
    source = _path(TRAVEL_ASSISTANT_DIR)
    has_agent = source.exists() and _has_backup_worthy_contents(source)
    has_root = any(_path(name).exists() for name in _root_overlay_targets())

    if not has_agent and not has_root:
        # Replace-with-empty: a revisited step the learner emptied must not keep a
        # stale, restorable backup around.
        if final.exists():
            try:
                shutil.rmtree(final)
            except OSError as exc:
                raise AdvanceError(f"Failed to clear stale backup {final}: {exc}") from exc
        return None

    staging = _reserve_backup_dir(f"step-{step}.staging")
    superseded: Path | None = None
    try:
        # Always capture the agent namespace so a restore reproduces travel_assistant/
        # exactly — including a lone .gitkeep — even when the only real work this
        # step was root-level.
        _backup_travel_assistant(staging)
        if has_root:
            _backup_root_targets(staging)
        _write_backup_manifest(staging, step)

        if final.exists():
            superseded = final.with_name(f"{final.name}.superseded")
            try:
                if superseded.exists():
                    shutil.rmtree(superseded)
                os.replace(final, superseded)
            except OSError as exc:
                raise AdvanceError(
                    f"Failed to set aside previous backup {final}: {exc}"
                ) from exc
        try:
            os.replace(staging, final)
        except OSError as exc:
            # Roll the prior snapshot back so a failed publish never loses it.
            if superseded is not None and not final.exists():
                os.replace(superseded, final)
            raise AdvanceError(f"Failed to publish backup {final}: {exc}") from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if superseded is not None:
        shutil.rmtree(superseded, ignore_errors=True)
    return final


def _restore_from_backup(backup_dir: Path) -> None:
    """Restore ``travel_assistant`` and root overlay targets from ``backup_dir``.

    New-format snapshots keep the agent snapshot under ``travel_assistant/`` and
    the repo-root overlay targets under ``_root/`` (see :func:`_snapshot_current_step`).
    Restoration is therefore structural — the agent namespace is copied back into
    ``travel_assistant/`` and each entry under ``_root/`` back to the repo root —
    with no name-based routing, so a learner file inside ``travel_assistant/``
    that happens to share a root-target name is never misrouted.

    Each destination is cleared immediately before it is copied, so the restore is
    an exact replacement even for a backup entry whose target is no longer declared
    by the current step files (e.g. after a template sync removed it) — the caller's
    clear pass keys off the *currently* declared targets and would otherwise leave
    such a directory to be merged into rather than replaced.
    """

    travel_dest = _path(TRAVEL_ASSISTANT_DIR)
    travel_source = backup_dir / BACKUP_AGENT_SUBDIR
    _replace_path_with(travel_source if travel_source.is_dir() else None, travel_dest)
    if not travel_dest.exists():
        try:
            travel_dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AdvanceError(f"Failed to create {travel_dest}: {exc}") from exc

    root_source = backup_dir / BACKUP_ROOT_SUBDIR
    if not root_source.is_dir():
        return
    try:
        entries = sorted(root_source.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise AdvanceError(f"Failed to read backup {root_source}: {exc}") from exc
    for entry in entries:
        _replace_path_with(entry, _path(entry.name))


def _validate_step_files_present(target_step: int) -> None:
    """Ensure every canonical step directory ``0..target_step`` exists.

    ``--back`` clears ``travel_assistant/`` before rebuilding, so a missing step
    directory must be fatal up front (advance merely warns). This runs before any
    destructive work so a broken template never leaves a half-rebuilt workspace.
    """

    missing: list[str] = []
    for step in range(0, target_step + 1):
        source = _path(STEP_FILES_DIR) / _step_dir_name(step)
        if not source.exists():
            missing.append(_step_dir_name(step))
        elif not source.is_dir():
            raise AdvanceError(
                f".workshop/step_files/{_step_dir_name(step)} exists but is not a directory."
            )
    if missing:
        joined = ", ".join(f".workshop/step_files/{name}/" for name in missing)
        raise AdvanceError(
            f"Cannot rebuild step {target_step}: missing canonical step files: {joined}."
        )


def _reserve_backup_dir(prefix: str) -> Path:
    """Create and return a unique backup directory named ``<prefix>-<timestamp>``.

    Uses an exclusive ``mkdir`` and appends ``-1``, ``-2``, ... on collision so
    two backups taken within the same UTC second (e.g. a rapid back-then-back)
    never merge into one directory. This keeps the "current work is always backed
    up first" guarantee true even under fast repeated runs.
    """

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    base = _path(BACKUPS_DIR) / f"{prefix}-{timestamp}"
    candidate = base
    suffix = 1
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base.with_name(f"{base.name}-{suffix}")
            suffix += 1
        except OSError as exc:
            raise AdvanceError(f"Failed to create backup directory {candidate}: {exc}") from exc


def _lay_down_step_files(step: int, *, clear_existing: bool = True) -> bool:
    """Copy canonical files for ``step`` into ``travel_assistant`` if available.

    When ``clear_existing`` is True (init/reset), ``travel_assistant`` is wiped
    first so a clean starter set is laid down. When False (advance), the step
    files are *overlaid* on top of the existing folder so the learner's work
    from earlier steps is preserved — the workshop stays incremental and code
    from previous steps is never deleted.
    """

    source = _path(STEP_FILES_DIR) / _step_dir_name(step)
    destination = _path(TRAVEL_ASSISTANT_DIR)
    if not source.exists():
        print(
            f"WARNING: no .workshop/step_files/{_step_dir_name(step)} to lay down — "
            "nothing changed in travel_assistant/."
        )
        return False
    if not source.is_dir():
        raise AdvanceError(f".workshop/step_files/{_step_dir_name(step)} exists but is not a directory.")
    if clear_existing:
        # Wipe first so a fresh init/reset starts from a clean starter set.
        # Tests guarantee we only reach here when step_files/<step>/ exists, so
        # this never silently empties the working folder.
        _clear_travel_assistant()
        _clear_root_targets()
    else:
        # Advance path: make sure the folder exists, then overlay the step files
        # on top of it without removing anything the learner carried forward.
        destination.mkdir(parents=True, exist_ok=True)
    # The reserved _root/ subfolder targets the repo root, not travel_assistant/,
    # so skip it here and lay it down separately as a sibling of travel_assistant/.
    _copy_tree(source, destination, ignore=shutil.ignore_patterns(ROOT_OVERLAY_DIR))
    _overlay_root_files(step)
    return True


def _clear_travel_assistant() -> None:
    """Remove all current contents from ``travel_assistant`` and recreate it."""

    directory = _path(TRAVEL_ASSISTANT_DIR)
    try:
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdvanceError(f"Failed to clear {directory}: {exc}") from exc


def _render_readme(step: int) -> str:
    """Render README content for ``step`` using detected repository metadata."""

    owner, repo = _detect_owner_repo()
    try:
        return render(step=step, owner=owner, repo=repo, terminal_step=TERMINAL_STEP)
    except Exception as exc:
        raise AdvanceError(f"Failed to render {README_FILE} for step {step}: {exc}") from exc


def _write_text(path: Path, text: str) -> None:
    """Write text to a path with a clear error on failure."""

    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise AdvanceError(f"Failed to write {path}: {exc}") from exc


def _write_state(step: int) -> None:
    """Persist the workshop state for ``step``."""

    payload = {"current_step": step, "schema_version": SCHEMA_VERSION}
    _write_text(_path(STATE_FILE), json.dumps(payload, indent=2) + "\n")


def _export_new_step(step: int) -> None:
    """Append ``NEW_STEP`` to the GitHub Actions environment file, when set."""

    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        return
    try:
        with Path(github_env).open("a", encoding="utf-8") as env_file:
            env_file.write(f"NEW_STEP={step}\n")
    except OSError as exc:
        raise AdvanceError(f"Failed to append NEW_STEP to GITHUB_ENV: {exc}") from exc


def _export_advanced(advanced: bool) -> None:
    """Append ``advanced=true|false`` to the GitHub Actions step-output file.

    The push-driven workflow reads this to decide whether to commit, push, and
    summarize. A no-op ``--on-push`` run (step 0/99 or uninitialized) emits
    ``false`` so the workflow skips the commit and never renders an empty
    ``NEW_STEP`` in its summary. No-op when ``GITHUB_OUTPUT`` is unset (local).
    """

    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    value = "true" if advanced else "false"
    try:
        with Path(github_output).open("a", encoding="utf-8") as output_file:
            output_file.write(f"advanced={value}\n")
    except OSError as exc:
        raise AdvanceError(f"Failed to append advanced to GITHUB_OUTPUT: {exc}") from exc


def _auto_commit(message: str) -> None:
    """Stage workshop-owned paths and commit them with ``message``.

    Restricted to the paths in :data:`_AUTO_COMMIT_PATHS` plus any discovered
    ``_root/`` overlay targets, so unrelated local edits or untracked files are
    never swept into a workshop commit. Candidates that no longer exist on disk
    but are still tracked are included so a deletion (e.g. a root overlay dir
    removed by ``--reset``) gets staged. Any failure (no git binary, not a git
    repo, missing identity, nothing to commit, push hook errors) is reported to
    stderr but does NOT raise, since the file changes are already persisted on
    disk and the user can finish the commit manually.
    """

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    try:
        inside = _run(["git", "rev-parse", "--is-inside-work-tree"])
    except FileNotFoundError:
        print(
            "WARNING: --auto-commit skipped because `git` was not found on PATH. "
            "Files were updated; commit manually when ready.",
            file=sys.stderr,
        )
        return

    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print(
            "WARNING: --auto-commit skipped because this is not a git working tree. "
            "Files were updated; commit manually once the repo is initialized.",
            file=sys.stderr,
        )
        return

    def _is_tracked(rel: str) -> bool:
        # A path that no longer exists on disk but is still tracked has a
        # deletion to stage, so include it in the pathspec (e.g. a root overlay
        # dir removed by --reset before --auto-commit runs).
        return _run(["git", "ls-files", "--error-unmatch", "--", rel]).returncode == 0

    candidates = list(_AUTO_COMMIT_PATHS) + sorted(_root_overlay_targets())
    existing = [
        rel for rel in candidates if (REPO_ROOT / rel).exists() or _is_tracked(rel)
    ]
    if not existing:
        print(
            "WARNING: --auto-commit found no workshop-owned paths to stage; "
            "skipping commit.",
            file=sys.stderr,
        )
        return

    add = _run(["git", "add", "--", *existing])
    if add.returncode != 0:
        print(
            "WARNING: --auto-commit could not stage workshop paths:\n"
            f"{add.stderr.strip() or add.stdout.strip()}\n"
            f"Files were updated; run `git add -- {' '.join(existing)}` "
            "and commit manually.",
            file=sys.stderr,
        )
        return

    diff = _run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print(
            "WARNING: --auto-commit found no workshop changes to commit "
            "(workshop files already match HEAD).",
            file=sys.stderr,
        )
        return

    commit = _run(["git", "commit", "-m", message])
    if commit.returncode != 0:
        detail = commit.stderr.strip() or commit.stdout.strip()
        print(
            "WARNING: --auto-commit could not create the commit:\n"
            f"{detail}\n"
            "Files were updated; commit manually with:\n"
            f"  git add -- {' '.join(existing)}\n"
            f"  git commit -m \"{message}\"",
            file=sys.stderr,
        )
        return

    print(f"Auto-committed: {message}")


def _detect_owner_repo() -> tuple[str, str]:
    """Detect GitHub owner/repo from ``origin``, or return placeholders."""

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return _PLACEHOLDER_OWNER, _PLACEHOLDER_REPO

    if result.returncode != 0:
        return _PLACEHOLDER_OWNER, _PLACEHOLDER_REPO

    match = _REMOTE_RE.search(result.stdout.strip())
    if match is None:
        return _PLACEHOLDER_OWNER, _PLACEHOLDER_REPO
    return match.group(1), match.group(2)


def _plan_advance(current_step: int, next_step: int) -> list[str]:
    """Return human-readable advance actions for dry-run output."""

    actions = [
        f"Would snapshot travel_assistant/ (and any workshop root files) to "
        f".workshop_instance/workshop_backups/step-{current_step}/, replacing any prior snapshot.",
        f"Would overlay .workshop/step_files/{_step_dir_name(next_step)}/ onto travel_assistant/ (keeping earlier work) if present.",
    ]
    overlay = _path(STEP_FILES_DIR) / _step_dir_name(next_step) / ROOT_OVERLAY_DIR
    if overlay.is_dir():
        actions.append(
            f"Would overlay .workshop/step_files/{_step_dir_name(next_step)}/{ROOT_OVERLAY_DIR}/ "
            "onto the repo root (as siblings of travel_assistant/)."
        )
    actions.extend(
        [
            f"Would render README.md for step {next_step}.",
            f"Would update {STATE_FILE} to current_step {next_step}.",
            f"Would export NEW_STEP={next_step} if GITHUB_ENV is set.",
        ]
    )
    return actions


def _advance(expected: str | None, *, dry_run: bool, auto_commit: bool) -> int:
    """Advance the workshop by one step."""

    current_step = _load_state()
    _validate_state_sync(current_step, _read_readme_marker())
    _validate_expected(expected, current_step)
    next_step = _compute_next_step(current_step)

    if dry_run:
        print(f"DRY RUN: advancing step {current_step} -> {next_step}")
        for action in _plan_advance(current_step, next_step):
            print(action)
        if auto_commit:
            print(
                f"Would auto-commit workshop-owned paths with message "
                f"'workshop: advance to step {next_step}'."
            )
        return 0

    backup_destination = _snapshot_current_step(current_step)
    if backup_destination is not None:
        print(
            "Backed up travel_assistant/ (and any workshop root files) to "
            f"{backup_destination.relative_to(REPO_ROOT)}"
        )
    _lay_down_step_files(next_step, clear_existing=False)
    _write_text(_path(README_FILE), _render_readme(next_step))
    _write_state(next_step)
    _export_new_step(next_step)
    print(f"Advanced workshop to step {next_step}: {STEP_TITLES.get(next_step, 'Unknown')}")
    if auto_commit:
        _auto_commit(f"workshop: advance to step {next_step}")
    return 0


def _advance_on_push(*, dry_run: bool) -> int:
    """Advance one step in response to a push to the default branch.

    This is the engine behind ``advance-on-push.yml``. It is deliberately
    tolerant at the workshop's boundaries so a routine push never fails a
    learner's build:

    - **Uninitialized** (no ``.workshop-state.json`` yet, and no init marker):
      no-op. ``init-workshop.yml`` owns first-time setup; racing it here would
      double-write step 0.
    - **State lost after init** (marker present but state missing/malformed):
      fail loudly — the repo is genuinely broken and silently succeeding would
      hide it.
    - **Step 0**: no-op. Setup advances only via the manual *Start the
      workshop* action, never on a push.
    - **Final step (99)**: no-op. The workshop is complete.
    - Otherwise: advance exactly one step, mirroring ``--expected`` with the
      guard set to the current step.

    Emits ``advanced=true|false`` to ``$GITHUB_OUTPUT`` so the workflow can gate
    its commit, push, and summary steps.
    """

    marker_present = _path(INIT_MARKER).exists()

    if not _path(STATE_FILE).exists():
        if marker_present:
            raise AdvanceError(
                f"Missing {STATE_FILE} but {INIT_MARKER} exists — workshop state "
                "was lost. Repair manually (restore the file or run Reset workshop)."
            )
        print(
            f"{STATE_FILE} is missing and the workshop is not initialized yet; "
            "nothing to advance."
        )
        _export_advanced(False)
        return 0

    try:
        current_step = _load_state()
    except AdvanceError:
        if marker_present:
            raise
        print(
            f"{STATE_FILE} is unreadable and the workshop is not initialized yet; "
            "nothing to advance."
        )
        _export_advanced(False)
        return 0

    if current_step == 0:
        print("Still on Setup (step 0). Click 'Start the workshop' to begin.")
        _export_advanced(False)
        return 0

    if current_step == FINAL_STEP:
        print("Workshop already complete. Use Reset workshop to restart.")
        _export_advanced(False)
        return 0

    _validate_state_sync(current_step, _read_readme_marker())
    next_step = _compute_next_step(current_step)

    if dry_run:
        print(f"DRY RUN: advancing step {current_step} -> {next_step}")
        for action in _plan_advance(current_step, next_step):
            print(action)
        _export_advanced(False)
        return 0

    backup_destination = _snapshot_current_step(current_step)
    if backup_destination is not None:
        print(
            "Backed up travel_assistant/ (and any workshop root files) to "
            f"{backup_destination.relative_to(REPO_ROOT)}"
        )
    _lay_down_step_files(next_step, clear_existing=False)
    _write_text(_path(README_FILE), _render_readme(next_step))
    _write_state(next_step)
    _export_new_step(next_step)
    _export_advanced(True)
    print(f"Advanced workshop to step {next_step}: {STEP_TITLES.get(next_step, 'Unknown')}")
    return 0


def _plan_init() -> list[str]:
    """Return human-readable init actions for dry-run output."""

    return [
        "Would clear travel_assistant/ and lay down .workshop/step_files/00/ (required; init hard-fails when missing).",
        "Would render README.md for step 0.",
        f"Would update {STATE_FILE} to current_step 0.",
        "Would export NEW_STEP=0 if GITHUB_ENV is set.",
    ]


def _file_contents_by_relpath(directory: Path) -> dict[str, bytes]:
    """Return a {relpath: file_bytes} fingerprint of every file under ``directory``.

    Used by ``--init`` to decide whether ``travel_assistant/`` is safe to clear.
    Empty directories are ignored — they are not meaningful in git and the
    safety check only cares about content that could be lost.
    """

    result: dict[str, bytes] = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(directory)).replace("\\", "/")
        try:
            result[rel] = path.read_bytes()
        except OSError as exc:
            raise AdvanceError(f"Failed to inspect {path}: {exc}") from exc
    return result


def _ensure_init_safe_to_clear(step_zero_source: Path) -> None:
    """Refuse ``--init`` if ``travel_assistant/`` has user content we'd lose.

    Allowed states (no refusal):
    - Directory missing or empty.
    - Only ``.gitkeep`` present — the canonical template placeholder.
    - Contents already match ``step_files/00/`` exactly — the idempotent retry
      case after a workflow crashed between the lay-down and the marker write.
    """

    travel_dir = _path(TRAVEL_ASSISTANT_DIR)
    if not travel_dir.exists():
        return
    # ``.gitkeep`` is the canonical template placeholder and is always safe to
    # drop because ``step_files/00/`` ships its own copy that immediately
    # replaces it. Ignoring it on both sides keeps the comparison symmetric.
    current = {p: c for p, c in _file_contents_by_relpath(travel_dir).items() if p != ".gitkeep"}
    if not current:
        return
    target = {
        p: c
        for p, c in _file_contents_by_relpath(step_zero_source).items()
        if p != ".gitkeep"
    }
    if current == target:
        return
    raise AdvanceError(
        "Cannot init: travel_assistant/ contains files that differ from the step 0 "
        "starter set. Use --reset to back up your edits before returning to step 0."
    )


def _init(*, dry_run: bool) -> int:
    """Apply step 0 for a freshly created template repository.

    Unlike ``--reset``, this never backs up ``travel_assistant`` because the
    template ships it empty. It only runs when the workshop is on step 0, so it
    cannot be used to silently throw away participant work from a later step.
    """

    current_step = _load_state()
    if current_step != 0:
        raise AdvanceError(
            f"Cannot init: workshop is on step {current_step}, expected 0. "
            "Use --reset to return to step 0 first."
        )

    # Hard-fail if the canonical step 0 starter files are missing, instead of
    # falling through to a warning from _lay_down_step_files. A successful init
    # is supposed to populate travel_assistant/ AND mark the repo initialized;
    # silently rendering the README without files would leave new repos in a
    # broken state with no participant signal that something went wrong.
    source = _path(STEP_FILES_DIR) / _step_dir_name(0)
    if not source.exists():
        raise AdvanceError(
            f"Cannot init: missing .workshop/step_files/{_step_dir_name(0)}/ — "
            "the template is missing its step 0 starter files."
        )
    if not source.is_dir():
        raise AdvanceError(f".workshop/step_files/{_step_dir_name(0)} exists but is not a directory.")

    # Refuse to clobber user edits in travel_assistant/. Runs before the
    # dry-run branch so that ``--init --dry-run`` accurately reports refusal
    # rather than printing a plan it would not actually be allowed to execute.
    _ensure_init_safe_to_clear(source)

    if dry_run:
        print("DRY RUN: applying step 0 for fresh template initialization")
        for action in _plan_init():
            print(action)
        return 0

    _lay_down_step_files(0)
    _write_text(_path(README_FILE), _render_readme(0))
    _write_state(0)
    _export_new_step(0)
    print("Initialized workshop at step 0: Setup")
    return 0


def _plan_reset(current_step: int) -> list[str]:
    """Return human-readable reset actions for dry-run output."""

    return [
        "Would back up travel_assistant/ to .workshop_instance/workshop_backups/reset-<timestamp>/.",
        "Would clear travel_assistant/.",
        "Would re-apply .workshop/step_files/00/ if present; otherwise create travel_assistant/.gitkeep.",
        "Would render README.md for step 0.",
        f"Would update {STATE_FILE} to current_step 0.",
        "Would export NEW_STEP=0 if GITHUB_ENV is set.",
        f"Current step is {current_step}.",
    ]


def _reset(*, dry_run: bool, auto_commit: bool) -> int:
    """Reset the workshop to step 0."""

    current_step = _load_state()
    _validate_state_sync(current_step, _read_readme_marker())

    if dry_run:
        print("DRY RUN: resetting workshop to step 0")
        for action in _plan_reset(current_step):
            print(action)
        if auto_commit:
            print(
                "Would auto-commit workshop-owned paths with message "
                "'workshop: reset to step 0'."
            )
        return 0

    backup_destination = _reserve_backup_dir("reset")
    if _backup_travel_assistant(backup_destination):
        print(f"Backed up travel_assistant/ to {backup_destination.relative_to(REPO_ROOT)}")
    if _backup_root_targets(backup_destination):
        print(f"Backed up workshop root files to {backup_destination.relative_to(REPO_ROOT)}")

    _clear_travel_assistant()
    _clear_root_targets()
    source = _path(STEP_FILES_DIR) / _step_dir_name(0)
    if source.exists():
        if not source.is_dir():
            raise AdvanceError(".workshop/step_files/00 exists but is not a directory.")
        _copy_tree(
            source,
            _path(TRAVEL_ASSISTANT_DIR),
            ignore=shutil.ignore_patterns(ROOT_OVERLAY_DIR),
        )
        _overlay_root_files(0)
    else:
        _write_text(_path(TRAVEL_ASSISTANT_DIR) / ".gitkeep", "")

    _write_text(_path(README_FILE), _render_readme(0))
    _write_state(0)
    _export_new_step(0)
    print("Reset workshop to step 0: Setup")
    if auto_commit:
        _auto_commit("workshop: reset to step 0")
    return 0


def _plan_back(current_step: int, previous_step: int, *, restore_from_backup: bool) -> list[str]:
    """Return human-readable back actions for dry-run output."""

    actions = [
        "Would back up travel_assistant/ (and workshop root files) to "
        ".workshop_instance/workshop_backups/back-<timestamp>/.",
        "Would clear travel_assistant/ and workshop root overlay targets.",
    ]
    if restore_from_backup:
        actions.append(
            "Would restore travel_assistant/ (and workshop root files) from "
            f".workshop_instance/workshop_backups/step-{previous_step}/ (your saved work)."
        )
    else:
        actions.append(
            f"No .workshop_instance/workshop_backups/step-{previous_step}/ backup found; "
            f"would rebuild canonical step files 0..{previous_step} onto travel_assistant/ "
            "(in-place edits from those steps are not restored)."
        )
    actions.extend(
        [
            f"Would render README.md for step {previous_step}.",
            f"Would update {STATE_FILE} to current_step {previous_step}.",
            f"Would export NEW_STEP={previous_step} if GITHUB_ENV is set.",
            f"Current step is {current_step}.",
        ]
    )
    return actions


def _back(*, dry_run: bool, auto_commit: bool) -> int:
    """Move the workshop back one step.

    Prefers restoring the learner's saved work from
    ``workshop_backups/step-<previous>/`` (the snapshot advance took before
    moving off that step). When that backup is absent — e.g. the learner cloned
    at a mid step or never advanced through the script — it falls back to
    rebuilding the canonical step files with a clear warning. Either way the
    current work is backed up first, so nothing is lost.
    """

    current_step = _load_state()
    _validate_state_sync(current_step, _read_readme_marker())
    previous_step = _compute_previous_step(current_step)

    backup_source = _path(BACKUPS_DIR) / f"step-{previous_step}"
    restore_from_backup = _is_restorable_backup(backup_source)
    # A directory without a valid completion manifest is a legacy flattened backup
    # (or a partial write): it cannot be restored safely, so we fall back to a
    # canonical rebuild but say so precisely.
    legacy_backup = not restore_from_backup and backup_source.is_dir()

    # Validate everything that could fail BEFORE any destructive work so a broken
    # template or render never leaves travel_assistant/ half-rebuilt.
    if not restore_from_backup:
        _validate_step_files_present(previous_step)
    readme_text = _render_readme(previous_step)

    if dry_run:
        print(f"DRY RUN: going back step {current_step} -> {previous_step}")
        for action in _plan_back(current_step, previous_step, restore_from_backup=restore_from_backup):
            print(action)
        if auto_commit:
            print(
                "Would auto-commit workshop-owned paths with message "
                f"'workshop: go back to step {previous_step}'."
            )
        return 0

    backup_destination = _reserve_backup_dir("back")
    if _backup_travel_assistant(backup_destination):
        print(f"Backed up travel_assistant/ to {backup_destination.relative_to(REPO_ROOT)}")
    if _backup_root_targets(backup_destination):
        print(f"Backed up workshop root files to {backup_destination.relative_to(REPO_ROOT)}")

    _clear_travel_assistant()
    _clear_root_targets()

    if restore_from_backup:
        _restore_from_backup(backup_source)
        print(f"Restored travel_assistant/ from {backup_source.relative_to(REPO_ROOT)}")
    else:
        reason = (
            f"a legacy {backup_source.relative_to(REPO_ROOT)} backup exists but predates the "
            "restorable backup format and cannot be safely restored"
            if legacy_backup
            else f"no saved {backup_source.relative_to(REPO_ROOT)} backup found"
        )
        print(
            f"WARNING: {reason}; rebuilt canonical starter files for step {previous_step}. "
            "In-place edits from those steps are not restored (your current work was backed "
            f"up to {backup_destination.relative_to(REPO_ROOT)}).",
            file=sys.stderr,
        )
        for step in range(0, previous_step + 1):
            _lay_down_step_files(step, clear_existing=False)

    _write_text(_path(README_FILE), readme_text)
    _write_state(previous_step)
    _export_new_step(previous_step)
    print(f"Moved workshop back to step {previous_step}: {STEP_TITLES.get(previous_step, 'Unknown')}")
    if auto_commit:
        _auto_commit(f"workshop: go back to step {previous_step}")
    return 0


def _plan_relay_current(current_step: int) -> list[str]:
    """Return human-readable reset-current actions for dry-run output."""

    name = STEP_TITLES.get(current_step, "")
    label = f"step {current_step}: {name}" if name else f"step {current_step}"
    return [
        "Would back up travel_assistant/ and workshop root files to "
        f".workshop_instance/workshop_backups/reset-current-{current_step:02d}-<timestamp>/.",
        "Would clear travel_assistant/ and re-lay the clean starter files for "
        f"{label} from .workshop/step_files/{_step_dir_name(current_step)}/.",
        f"Would re-render README.md for {label}.",
        f"Would leave {STATE_FILE} unchanged at current_step {current_step}.",
        f"Would export NEW_STEP={current_step} if GITHUB_ENV is set.",
    ]


def _relay_current(*, dry_run: bool, auto_commit: bool) -> int:
    """Re-lay the *current* step's clean starter files and re-render its README.

    Unlike ``--reset`` (which drops back to step 0), this keeps the learner on
    their current step but discards local edits to travel_assistant/ in favor of
    the canonical starter files, after backing that work up. It is the companion
    to a template sync: sync refreshes the machinery, reset-current refreshes the
    current step's delivery files and instructions.
    """

    current_step = _load_state()
    _validate_state_sync(current_step, _read_readme_marker())

    # Fail loudly if this step ships no starter files: without them we would back
    # up and wipe travel_assistant/ (via _lay_down_step_files) yet lay nothing
    # back down, leaving the learner with an empty folder while reporting success.
    source = _path(STEP_FILES_DIR) / _step_dir_name(current_step)
    if not source.is_dir():
        raise AdvanceError(
            f"Cannot reset current step {current_step}: "
            f".workshop/step_files/{_step_dir_name(current_step)}/ is missing. "
            "The authoring material for this step is incomplete — nothing was changed."
        )

    commit_message = f"workshop: reset current step {current_step} {SKIP_ADVANCE_SENTINEL}"
    if dry_run:
        print(f"DRY RUN: resetting current step {current_step} to its clean starter files")
        for action in _plan_relay_current(current_step):
            print(action)
        if auto_commit:
            print(f"Would auto-commit workshop-owned paths with message '{commit_message}'.")
        return 0

    backup_destination = _reserve_backup_dir(f"reset-current-{current_step:02d}")
    if _backup_travel_assistant(backup_destination):
        print(f"Backed up travel_assistant/ to {backup_destination.relative_to(REPO_ROOT)}")
    if _backup_root_targets(backup_destination):
        print(f"Backed up workshop root files to {backup_destination.relative_to(REPO_ROOT)}")

    _lay_down_step_files(current_step, clear_existing=True)
    _write_text(_path(README_FILE), _render_readme(current_step))
    # State is already on current_step; rewrite it so schema/format stays canonical.
    _write_state(current_step)
    _export_new_step(current_step)
    name = STEP_TITLES.get(current_step, "")
    label = f"step {current_step}: {name}" if name else f"step {current_step}"
    print(f"Reset current step to clean starter files: {label}")
    if auto_commit:
        _auto_commit(commit_message)
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(description="Advance, reset, or initialize the workshop step.")
    parser.add_argument(
        "--expected",
        "--expected-current-step",
        dest="expected",
        default=None,
        help=(
            "Expected current step before advancing. Required by the GitHub "
            "Actions workflow to guard against races; optional when running "
            "locally."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--reset", action="store_true", help="Reset the workshop to step 0.")
    mode_group.add_argument(
        "--reset-current",
        dest="reset_current",
        action="store_true",
        help=(
            "Re-lay the CURRENT step's clean starter files and re-render its "
            "README, backing up travel_assistant/ first. Stays on the current "
            "step (unlike --reset, which returns to step 0). Pairs with a "
            "template sync when you want the current step refreshed too."
        ),
    )
    mode_group.add_argument(
        "--init",
        action="store_true",
        help="Initialize a fresh template repository at step 0 without backing up travel_assistant/.",
    )
    mode_group.add_argument(
        "--back",
        action="store_true",
        help=(
            "Move back one workshop step. Restores your saved work from "
            ".workshop_instance/workshop_backups/step-<N>/ when present, otherwise "
            "rebuilds the canonical step files. Current work is backed up first. "
            "Errors at step 0."
        ),
    )
    mode_group.add_argument(
        "--on-push",
        dest="on_push",
        action="store_true",
        help=(
            "Advance in response to a push to the default branch: no-op before "
            "initialization and at step 0 or the final step, advance exactly one "
            "step otherwise. Emits advanced=true|false to $GITHUB_OUTPUT."
        ),
    )
    mode_group.add_argument(
        "--check-machinery-only",
        dest="check_machinery_only",
        action="store_true",
        help=(
            "Read newline-separated repo paths from stdin and print 'true' when "
            "they are ALL workshop machinery/platform files (so a push touching "
            "only them must not advance the step), otherwise 'false'. Used by "
            "advance-on-push.yml to guard manual machinery pushes."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions only.")
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help=(
            "After advancing or resetting, stage the workshop-owned paths "
            "(README.md, .workshop_instance/.workshop-state.json, travel_assistant/, "
            ".workshop_instance/workshop_backups/) and create a commit. Unrelated "
            "local edits are never staged. No-op under --dry-run."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.check_machinery_only:
            return _run_check_machinery_only()
        if args.init:
            return _init(dry_run=args.dry_run)
        if args.reset:
            return _reset(dry_run=args.dry_run, auto_commit=args.auto_commit)
        if args.back:
            return _back(dry_run=args.dry_run, auto_commit=args.auto_commit)
        if args.reset_current:
            return _relay_current(dry_run=args.dry_run, auto_commit=args.auto_commit)
        if args.on_push:
            return _advance_on_push(dry_run=args.dry_run)
        return _advance(args.expected, dry_run=args.dry_run, auto_commit=args.auto_commit)
    except AdvanceError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
