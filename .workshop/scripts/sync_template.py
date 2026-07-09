"""Sync workshop infrastructure from the upstream template into this instance.

A workshop *instance* (a repo created from the template) sometimes needs the
latest workshop machinery — the authoring material under ``.workshop/`` and the
GitHub configuration under ``.github/`` — without touching the participant's own
progress. This CLI mirrors exactly those two trees from a chosen upstream ref
into the working tree and index, leaving everything else alone:

- ``travel_assistant/`` (the delivery the participant builds),
- ``.workshop_instance/`` (the per-instance step state + backups),
- ``README.md`` and ``.env`` (rendered / local values)

are never in the sync set, so the current step is preserved. Because the
mirrored paths are staged (including files deleted upstream), the caller can
commit and push them as one changeset.

Crucially, the sync commit carries a ``[skip-advance]`` sentinel in its message.
``advance-on-push.yml`` looks for that sentinel and skips advancing, so pushing a
sync to ``main`` refreshes the machinery **without moving to the next step**.

This module is stdlib-only so it runs in GitHub Actions without installing the
workshop dependencies.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

# This script lives at .workshop/scripts/sync_template.py, so the repository root
# is two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]

# The upstream template every instance is created from. Overridable so forks of
# the template can point instances at their own fork.
DEFAULT_UPSTREAM_URL = "https://github.com/Azure-Samples/foundry-hosted-agents-workshop.git"
DEFAULT_REF = "main"

# Only the workshop machinery is synced. Everything else — the participant's
# delivery and their per-instance state — stays exactly as it is.
DEFAULT_SYNC_PATHS = (".workshop", ".github")

# Paths that must never be synced, even if explicitly passed, because they hold
# participant work or per-instance state. Overwriting them would clobber progress
# or reset the current step.
PROTECTED_PATHS = frozenset(
    {
        ".workshop_instance",
        "travel_assistant",
        "README.md",
        ".env",
    }
)

# Sentinel embedded in every sync commit message. advance-on-push.yml greps for
# this exact string and skips advancing when it is present, so a sync push never
# moves the workshop to the next step. Keep it in sync with the check in
# .github/workflows/advance-on-push.yml.
SKIP_ADVANCE_SENTINEL = "[skip-advance]"
COMMIT_MESSAGE = f"workshop: sync template infra from upstream {SKIP_ADVANCE_SENTINEL}"

# The workflow that enforces the "sync must not advance" contract. Before we
# adopt a new .github/ tree we verify the upstream copy of this file still honors
# SKIP_ADVANCE_SENTINEL — otherwise a sync could replace the guard with a version
# that advances the step (see _verify_upstream_guard).
GUARD_WORKFLOW = ".github/workflows/advance-on-push.yml"

# Characters that would turn a sync path into a git pathspec pattern rather than
# a literal path. We reject them so --paths can never widen the sync set beyond a
# plain, in-repo directory or file. A leading ':' introduces pathspec magic
# (e.g. ':(top)', ':(glob)'), which we reject separately.
_GLOB_METACHARS = frozenset("*?[]")

# Least privilege: _git is the single choke point for git, so it only permits
# the exact subcommands this script needs. A future refactor (or a bug) cannot
# reach for a destructive or unexpected operation — reset, clean, filter-branch,
# an arbitrary push target, etc. — without a deliberate, reviewed addition here.
_ALLOWED_GIT_SUBCOMMANDS = frozenset(
    {
        "remote",  # read origin URL (self-sync guard)
        "fetch",  # pull the upstream ref into FETCH_HEAD
        "show",  # read a file out of the fetched tree (guard check)
        "diff",  # report staged / upstream-vs-HEAD changes
        "rm",  # stage deletions when mirroring
        "checkout",  # restore files from the fetched tree
        "commit",  # commit the mirrored changes (--commit)
        "push",  # push the sync commit (--push)
    }
)


class SyncError(RuntimeError):
    """Raised when the template cannot be synced safely."""


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted git command in the repository root.

    Only the subcommands in ``_ALLOWED_GIT_SUBCOMMANDS`` may run; anything else
    raises :class:`SyncError` before touching the repository.
    """

    if not args or args[0] not in _ALLOWED_GIT_SUBCOMMANDS:
        raise SyncError(f"Refusing to run non-allowlisted git command: {args!r}")

    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def _normalize_remote(url: str) -> str:
    """Normalize a git remote URL to ``host/owner/repo`` for comparison.

    Handles the common equivalent forms so the self-sync guard is not fooled by
    scheme, credentials, or scp-style syntax:
      - ``https://user:token@github.com/o/r.git`` -> ``github.com/o/r``
      - ``git@github.com:o/r.git``                -> ``github.com/o/r``
      - ``ssh://git@github.com/o/r``              -> ``github.com/o/r``
    Local filesystem paths normalize to themselves (lowercased), which is enough
    for the guard to match an identical path against itself.
    """

    normalized = url.strip().lower()
    normalized = re.sub(r"^[a-z][a-z0-9+.-]*://", "", normalized)  # strip scheme
    if "@" in normalized:  # strip user[:password]@ credentials
        normalized = normalized.split("@", 1)[1]
    normalized = normalized.replace(":", "/")  # scp host:path and drive letters
    if normalized.endswith(".git"):
        normalized = normalized[: -len(".git")]
    normalized = re.sub(r"/+", "/", normalized).strip("/")
    return normalized


def _origin_url() -> str | None:
    """Return the ``origin`` remote URL, or None when it cannot be read."""

    result = _git("remote", "get-url", "origin", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _validate_paths(paths: Sequence[str]) -> list[str]:
    """Reject empty, absolute, escaping, or protected sync paths.

    Zero-trust: the sync set is the only thing this script is allowed to
    overwrite, so we refuse anything that could reach outside the repo or step
    on participant work / per-instance state.
    """

    if not paths:
        raise SyncError("No sync paths provided; nothing to do.")

    validated: list[str] = []
    for raw in paths:
        candidate = raw.strip().replace("\\", "/").rstrip("/")
        if not candidate:
            raise SyncError("Sync paths must be non-empty.")
        if candidate.startswith(":"):
            raise SyncError(f"Refusing git pathspec magic in sync path: {raw!r}")
        if any(ch in _GLOB_METACHARS for ch in candidate):
            raise SyncError(f"Refusing glob characters in sync path: {raw!r}")
        # Normalize with PurePosixPath so equivalent spellings ('.github/.',
        # '.github//workflows', '.github/workflows/.') collapse to one canonical
        # form. Returning the canonical form is what lets _guard_paths_synced
        # reliably detect that a custom --paths still touches the advance guard.
        pure = PurePosixPath(candidate)
        if pure.is_absolute() or ".." in pure.parts:
            raise SyncError(f"Refusing unsafe sync path outside the repo: {raw!r}")
        parts = pure.parts
        if not parts or parts == (".",):
            raise SyncError("Refusing to sync the entire repository ('.').")
        normalized = pure.as_posix()
        top = parts[0]
        if normalized in PROTECTED_PATHS or top in PROTECTED_PATHS:
            raise SyncError(
                f"Refusing to sync protected path {raw!r}; "
                "it holds participant work or per-instance state."
            )
        validated.append(normalized)
    return validated


def _fetch_upstream(upstream_url: str, ref: str) -> None:
    """Fetch ``ref`` from ``upstream_url`` so ``FETCH_HEAD`` points at it."""

    result = _git("fetch", "--quiet", upstream_url, ref, check=False)
    if result.returncode != 0:
        raise SyncError(
            f"Failed to fetch {ref!r} from {upstream_url}:\n{result.stderr.strip()}"
        )


def _changed_files(paths: Sequence[str], *, staged: bool) -> list[str]:
    """Return files that differ for ``paths`` (staged index, or HEAD vs upstream)."""

    if staged:
        args = ["diff", "--cached", "--name-only", "--", *paths]
    else:
        args = ["diff", "--name-only", "HEAD", "FETCH_HEAD", "--", *paths]
    result = _git(*args)
    return [line for line in result.stdout.splitlines() if line.strip()]


def _guard_content_honors_sentinel(content: str) -> bool:
    """Heuristically confirm ``advance-on-push.yml`` acts on the sentinel.

    Text inspection cannot prove runtime behavior, but requiring the sentinel to
    co-occur with the skip mechanism (``proceed=false``) *and* a construct that
    consumes it (a fixed-string ``grep`` or a jq ``contains(``) rejects the weak
    case the reviewer flagged: the sentinel sitting in a comment or doc string
    while the actual guard logic has been removed. Keep these tokens in sync with
    the guard step in ``.github/workflows/advance-on-push.yml``.
    """

    if SKIP_ADVANCE_SENTINEL not in content:
        return False
    if "proceed=false" not in content:
        return False
    return "grep -qF" in content or "contains(" in content


def _guard_paths_synced(validated: Sequence[str]) -> bool:
    """Return True when the sync set includes the advance guard workflow."""

    return any(
        GUARD_WORKFLOW == p or GUARD_WORKFLOW.startswith(p + "/") for p in validated
    )


def _verify_upstream_guard(validated: Sequence[str], *, allow_missing_guard: bool) -> None:
    """Ensure the fetched upstream still honors the no-advance sentinel.

    The whole "sync without advancing" contract relies on the *post-sync* copy of
    ``advance-on-push.yml`` skipping commits that carry ``SKIP_ADVANCE_SENTINEL``.
    Because a sync overwrites ``.github/`` with the upstream tree, syncing from a
    ref whose ``advance-on-push.yml`` predates (or drops) that guard would replace
    the guard with a version that advances the step. Abort before mutating unless
    the caller explicitly accepts the risk.
    """

    if not _guard_paths_synced(validated):
        return

    result = _git("show", f"FETCH_HEAD:{GUARD_WORKFLOW}", check=False)
    if result.returncode != 0:
        message = (
            f"Upstream is missing {GUARD_WORKFLOW}; adopting its .github/ tree "
            "could remove the guard that stops a sync from advancing the step."
        )
    elif not _guard_content_honors_sentinel(result.stdout):
        message = (
            f"Upstream {GUARD_WORKFLOW} does not appear to honor the "
            f"{SKIP_ADVANCE_SENTINEL} sentinel (the sentinel is absent, or present "
            "only as text with no skip logic), so adopting it could let a sync "
            "advance the step."
        )
    else:
        return

    if allow_missing_guard:
        print(f"WARNING: {message} Proceeding because --allow-missing-guard was set.")
        return
    raise SyncError(
        f"{message}\nPoint --ref at a template revision that contains the guard, "
        "or pass --allow-missing-guard if you understand the risk."
    )


def _mirror_path(path: str) -> None:
    """Stage ``path`` to exactly match the fetched upstream tree.

    Removing the tracked copy first (then restoring from ``FETCH_HEAD``) means
    files deleted upstream are staged as deletions rather than lingering.
    Untracked local files are left untouched by ``git rm``.
    """

    _git("rm", "-r", "-q", "--ignore-unmatch", "--", path)
    restore = _git("checkout", "FETCH_HEAD", "--", path, check=False)
    if restore.returncode != 0:
        stderr = restore.stderr.strip()
        # A missing pathspec means upstream no longer ships this tree at all;
        # the removal above already mirrored that. Anything else is a real error.
        if "did not match" in stderr or "pathspec" in stderr:
            print(f"  {path}: removed (no longer present upstream)")
            return
        raise SyncError(f"Failed to restore {path} from upstream:\n{stderr}")


def _commit(message: str) -> bool:
    """Commit staged changes. Returns False when there is nothing to commit."""

    if not _git("diff", "--cached", "--name-only").stdout.strip():
        return False
    _git("commit", "-q", "-m", message)
    return True


def sync(
    *,
    upstream_url: str = DEFAULT_UPSTREAM_URL,
    ref: str = DEFAULT_REF,
    paths: Sequence[str] = DEFAULT_SYNC_PATHS,
    commit: bool = False,
    push: bool = False,
    allow_self: bool = False,
    allow_missing_guard: bool = False,
    dry_run: bool = False,
) -> int:
    """Mirror ``paths`` from ``upstream_url@ref`` into this instance.

    Returns 0 on success (including a clean no-op). Raises :class:`SyncError`
    when the sync cannot proceed safely.
    """

    validated = _validate_paths(paths)

    origin = _origin_url()
    if origin and _normalize_remote(origin) == _normalize_remote(upstream_url) and not allow_self:
        print(
            "Upstream matches this repo's origin — this looks like the template "
            "itself, not an instance. Nothing to sync. Pass --allow-self to override."
        )
        return 0

    print(f"Fetching {ref!r} from {upstream_url} ...")
    _fetch_upstream(upstream_url, ref)

    if dry_run:
        changed = _changed_files(validated, staged=False)
        if not changed:
            print("DRY RUN: already up to date; no changes to sync.")
            return 0
        print(f"DRY RUN: {len(changed)} file(s) would change:")
        for name in changed:
            print(f"  {name}")
        return 0

    _verify_upstream_guard(validated, allow_missing_guard=allow_missing_guard)

    print(f"Mirroring {', '.join(validated)} from upstream ...")
    for path in validated:
        _mirror_path(path)

    changed = _changed_files(validated, staged=True)
    if not changed:
        print("Already up to date; no changes to sync.")
        return 0

    print(f"Staged {len(changed)} changed file(s):")
    print(_git("diff", "--cached", "--stat", "--", *validated).stdout.rstrip())

    if commit:
        if _commit(COMMIT_MESSAGE):
            print(f"Committed: {COMMIT_MESSAGE}")
        if push:
            _git("push")
            print("Pushed to the current branch.")
    else:
        print(
            "\nChanges are staged but not committed. Commit with the "
            f"{SKIP_ADVANCE_SENTINEL} sentinel so the push does not advance the step, e.g.:\n"
            f'  git commit -m "{COMMIT_MESSAGE}"\n'
            "  git push"
        )
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Sync .workshop/ and .github/ from the upstream workshop template "
            "without advancing the current step."
        )
    )
    parser.add_argument(
        "--upstream-url",
        default=DEFAULT_UPSTREAM_URL,
        help=f"Upstream template git URL (default: {DEFAULT_UPSTREAM_URL}).",
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help=f"Upstream branch or tag to sync from (default: {DEFAULT_REF}).",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=list(DEFAULT_SYNC_PATHS),
        help="Repository paths to mirror (default: .workshop .github).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=f"Commit the staged changes with the {SKIP_ADVANCE_SENTINEL} sentinel.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push after committing (implies --commit).",
    )
    parser.add_argument(
        "--allow-self",
        action="store_true",
        help="Allow syncing when upstream matches origin (running in the template).",
    )
    parser.add_argument(
        "--allow-missing-guard",
        action="store_true",
        help=(
            "Proceed even if the upstream advance-on-push.yml lacks the "
            f"{SKIP_ADVANCE_SENTINEL} guard (may allow a sync to advance the step)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which files would change without modifying anything.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return sync(
            upstream_url=args.upstream_url,
            ref=args.ref,
            paths=args.paths,
            commit=args.commit or args.push,
            push=args.push,
            allow_self=args.allow_self,
            allow_missing_guard=args.allow_missing_guard,
            dry_run=args.dry_run,
        )
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
