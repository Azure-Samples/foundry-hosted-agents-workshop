"""Initialize a workshop repository created from the template.

This CLI is invoked by ``init-workshop.yml`` (on push/create/dispatch) and by
``start-workshop.yml`` (self-heal before leaving Setup). It is intentionally
idempotent so duplicate or accidental runs are safe: a marker file under
``.workshop_instance/`` short-circuits subsequent invocations to a no-op.

Responsibilities:
- Render ``README.md`` for the current workshop step with the real owner/repo.
- Substitute literal ``{{OWNER}}`` / ``{{REPO}}`` placeholders in ``README.md``
  and every ``docs/**/*.md`` file (covers anything the renderer doesn't emit).
- Create ``.workshop_instance/.workshop-initialized`` so future runs are no-ops.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).parent))
from render_readme import render

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ".workshop_instance/.workshop-state.json"
README_FILE = "README.md"
DOCS_DIR = ".workshop/docs"
MARKER_FILE = ".workshop_instance/.workshop-initialized"
_PLACEHOLDER_OWNER = "{{OWNER}}"
_PLACEHOLDER_REPO = "{{REPO}}"


class InitError(RuntimeError):
    """Raised when initialization cannot proceed."""


def _path(relative_path: str) -> Path:
    """Resolve a repository-relative path."""

    return REPO_ROOT / relative_path


def _marker_path() -> Path:
    """Return the absolute path of the initialization marker file."""

    return _path(MARKER_FILE)


def is_initialized() -> bool:
    """Return True when the marker file is present."""

    return _marker_path().exists()


def _load_current_step() -> int:
    """Read ``current_step`` from ``.workshop-state.json``."""

    state_path = _path(STATE_FILE)
    try:
        raw = state_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InitError(f"Missing {STATE_FILE}; cannot determine workshop step.") from exc
    except OSError as exc:
        raise InitError(f"Failed to read {STATE_FILE}: {exc}") from exc

    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InitError(f"Malformed {STATE_FILE}: {exc}") from exc

    if not isinstance(state, dict):
        raise InitError(f"Malformed {STATE_FILE}: expected a JSON object.")
    current_step = state.get("current_step")
    if not isinstance(current_step, int):
        raise InitError(f"Malformed {STATE_FILE}: current_step must be an integer.")
    return current_step


def _substitute_placeholders(text: str, owner: str, repo: str) -> str:
    """Replace literal owner/repo placeholders in ``text``."""

    return text.replace(_PLACEHOLDER_OWNER, owner).replace(_PLACEHOLDER_REPO, repo)


def _files_to_substitute() -> list[Path]:
    """Collect markdown files whose placeholders should be substituted."""

    targets: list[Path] = []
    readme = _path(README_FILE)
    if readme.exists():
        targets.append(readme)
    docs_dir = _path(DOCS_DIR)
    if docs_dir.exists():
        targets.extend(sorted(docs_dir.rglob("*.md")))
    return targets


def _substitute_files(owner: str, repo: str) -> list[Path]:
    """Rewrite placeholder occurrences in every markdown file. Returns changed paths."""

    changed: list[Path] = []
    for path in _files_to_substitute():
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InitError(f"Failed to read {path}: {exc}") from exc
        replaced = _substitute_placeholders(original, owner, repo)
        if replaced != original:
            try:
                path.write_text(replaced, encoding="utf-8")
            except OSError as exc:
                raise InitError(f"Failed to write {path}: {exc}") from exc
            changed.append(path)
    return changed


def _render_readme(step: int, *, owner: str, repo: str) -> None:
    """Render and write ``README.md`` for ``step``."""

    try:
        rendered = render(step=step, owner=owner, repo=repo)
    except Exception as exc:
        raise InitError(f"Failed to render {README_FILE} for step {step}: {exc}") from exc
    try:
        _path(README_FILE).write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise InitError(f"Failed to write {README_FILE}: {exc}") from exc


def _create_marker() -> None:
    """Create the ``.workshop_instance/.workshop-initialized`` marker file."""

    marker = _marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
    except OSError as exc:
        raise InitError(f"Failed to create marker {marker}: {exc}") from exc


def _validate_repo_arg(value: str, *, flag: str) -> str:
    """Reject empty or placeholder owner/repo arguments."""

    stripped = value.strip()
    if not stripped:
        raise InitError(f"{flag} must be a non-empty string.")
    if stripped in (_PLACEHOLDER_OWNER, _PLACEHOLDER_REPO):
        raise InitError(
            f"{flag} cannot be the literal placeholder {stripped}; "
            "pass the real owner/repo so substitution produces working URLs."
        )
    return stripped


def initialize(*, owner: str, repo: str, dry_run: bool = False) -> int:
    """Perform initialization, or report the plan when ``dry_run`` is set."""

    owner = _validate_repo_arg(owner, flag="--owner")
    repo = _validate_repo_arg(repo, flag="--repo")

    if is_initialized():
        print(f"Workshop already initialized (marker {MARKER_FILE} present); nothing to do.")
        return 0

    current_step = _load_current_step()

    if dry_run:
        print(f"DRY RUN: would initialize workshop for {owner}/{repo} at step {current_step}")
        print(f"Would render {README_FILE} for step {current_step}.")
        print(f"Would substitute placeholders in {README_FILE} and {DOCS_DIR}/**/*.md.")
        print(f"Would create marker {MARKER_FILE}.")
        return 0

    _render_readme(current_step, owner=owner, repo=repo)
    changed = _substitute_files(owner, repo)
    _create_marker()
    print(
        f"Initialized workshop for {owner}/{repo} at step {current_step} "
        f"(substituted placeholders in {len(changed)} file(s); created {MARKER_FILE})."
    )
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(description="Initialize the workshop repository.")
    parser.add_argument("--owner", required=True, help="GitHub repository owner.")
    parser.add_argument("--repo", required=True, help="GitHub repository name.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions only.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return initialize(owner=args.owner, repo=args.repo, dry_run=args.dry_run)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
