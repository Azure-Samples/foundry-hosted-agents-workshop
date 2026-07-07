"""Render the workshop README for a specific step.

This module combines shared Markdown partials with the step body under
``docs/steps``. It is intentionally stdlib-only so it can run in GitHub Actions
without installing workshop dependencies.
"""

from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
PARTIALS_DIR = REPO_ROOT / ".workshop" / "docs" / "partials"
STEPS_DIR = REPO_ROOT / ".workshop" / "docs" / "steps"
TERMINAL_STEP = 9
FINAL_STEP = 99

STEP_TITLES = {
    0: "Setup",
    1: "Basic hosted agent",
    2: "Function tools",
    3: "MCP integration",
    4: "Foundry Toolbox",
    5: "RAG (Azure AI Search)",
    6: "Skills",
    7: "Multi-agent",
    8: "Workflow (experimental)",
    9: "Memory (experimental)",
    99: "Cleanup",
}

_STEP_MARKER_RE = re.compile(r"<!--\s*step:\s*(\d+)\s*-->", re.IGNORECASE)
_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_PLACEHOLDER_OWNER = "{{OWNER}}"
_PLACEHOLDER_REPO = "{{REPO}}"


def parse_step_marker(readme_text: str) -> int | None:
    """Return the first ``<!-- step: N -->`` marker in ``readme_text``.

    Returns ``None`` when no marker is present.
    """

    match = _STEP_MARKER_RE.search(readme_text)
    if match is None:
        return None
    return int(match.group(1))


def render(
    step: int,
    *,
    owner: str,
    repo: str,
    terminal_step: int = TERMINAL_STEP,
) -> str:
    """Render the README Markdown for ``step``.

    The final cleanup step (``99``) is rendered without the next-step footer and
    is not counted in ``terminal_step``.
    """

    sections = [
        _load_header(step, terminal_step=terminal_step),
        _load_step_body(step, owner=owner, repo=repo),
    ]
    if step != FINAL_STEP:
        sections.append(
            _load_footer(
                step,
                owner=owner,
                repo=repo,
                terminal_step=terminal_step,
            )
        )
    return "\n\n\n".join(section.strip("\n") for section in sections) + "\n"


def _load_header(step: int, *, terminal_step: int = TERMINAL_STEP) -> str:
    """Load and substitute placeholders in the shared header partial."""

    header = (PARTIALS_DIR / "_header.md").read_text(encoding="utf-8")
    step_total = FINAL_STEP if step == FINAL_STEP else terminal_step
    replacements = {
        "{{STEP_NUMBER}}": _format_step_number(step),
        "{{STEP_TITLE}}": _step_title(step),
        "{{STEP_TOTAL}}": str(step_total),
        "{{PROGRESS_BAR}}": _progress_bar(step, terminal_step=terminal_step),
        "{{WORKSHOP_MAP}}": _workshop_map(step, terminal_step=terminal_step),
    }
    return _replace_placeholders(header, replacements)


def _load_footer(
    step: int,
    *,
    owner: str,
    repo: str,
    terminal_step: int = TERMINAL_STEP,
) -> str:
    """Load and substitute placeholders in the step's advance footer partial.

    Step 0 (Setup) uses the manual *Start the workshop* button
    (``_start_button.md``); every later numbered step advances by committing and
    pushing to the default branch (``_push_to_advance.md``). The final cleanup
    step renders no footer at all (handled by :func:`render`).
    """

    next_step = FINAL_STEP if step == terminal_step else step + 1
    partial_name = "_start_button.md" if step == 0 else "_push_to_advance.md"
    footer = (PARTIALS_DIR / partial_name).read_text(encoding="utf-8")
    replacements = {
        "{{OWNER}}": owner,
        "{{REPO}}": repo,
        "{{CURRENT_STEP}}": str(step),
        "{{NEXT_STEP_NUMBER}}": _format_step_number(next_step),
        "{{NEXT_STEP_TITLE}}": _step_title(next_step),
    }
    return _replace_placeholders(footer, replacements)


def _load_step_body(step: int, *, owner: str = "", repo: str = "") -> str:
    """Load ``docs/steps/<NN>-*.md``, validate any embedded step marker, and
    substitute ``{{OWNER}}``, ``{{REPO}}``, and ``{{CURRENT_STEP}}`` so that
    step bodies can embed workflow-dispatch links or other repo-specific URLs.
    """

    pattern = str(STEPS_DIR / f"{_format_step_number(step)}-*.md")
    matches = sorted(Path(path) for path in glob.glob(pattern))
    if not matches:
        raise RuntimeError(
            f"Missing step file for step {step}: expected {pattern}"
        )

    body = matches[0].read_text(encoding="utf-8")
    marker = parse_step_marker(body)
    if marker is not None and marker != step:
        raise RuntimeError(
            f"Step marker mismatch in {matches[0]}: found {marker}, expected {step}"
        )

    if owner and repo:
        body = _replace_placeholders(body, {
            "{{OWNER}}": owner,
            "{{REPO}}": repo,
            "{{CURRENT_STEP}}": str(step),
        })

    return body


def _progress_bar(step: int, *, terminal_step: int = TERMINAL_STEP) -> str:
    """Return a 10-character progress bar for ``step``.

    The cleanup step is displayed as complete while remaining excluded from the
    total step count.
    """

    if terminal_step <= 0:
        raise ValueError("terminal_step must be greater than zero")

    effective_step = terminal_step if step == FINAL_STEP else step
    clamped_step = max(0, min(effective_step, terminal_step))
    filled = int((clamped_step / terminal_step) * 10)
    filled = max(0, min(filled, 10))
    return "▰" * filled + "▱" * (10 - filled)


def _workshop_map(step: int, *, terminal_step: int = TERMINAL_STEP) -> str:
    """Build the workshop map with completed and current-step indicators."""

    step_numbers = list(range(0, terminal_step + 1))
    if FINAL_STEP not in step_numbers:
        step_numbers.append(FINAL_STEP)

    lines: list[str] = []
    for number in step_numbers:
        label = f"Step {_format_step_number(number)} — {_step_title(number)}"
        if number == step:
            label = f"**{label}**"
        if number < step:
            label = f"{label} ✅"
        lines.append(f"- {label}")
    return "\n".join(lines)


def _detect_owner_repo() -> tuple[str, str]:
    """Detect the GitHub owner and repository from the ``origin`` remote.

    Falls back to literal placeholders when no parseable GitHub remote exists.
    """

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

    remote_url = result.stdout.strip()
    match = _REMOTE_RE.search(remote_url)
    if match is None:
        return _PLACEHOLDER_OWNER, _PLACEHOLDER_REPO

    return match.group(1), match.group(2)


def _replace_placeholders(text: str, replacements: dict[str, str]) -> str:
    """Apply literal placeholder replacements to ``text``."""

    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def _format_step_number(step: int) -> str:
    """Format a step number as the two-digit workshop prefix."""

    return f"{step:02d}"


def _step_title(step: int) -> str:
    """Return the configured title for ``step`` or a generic fallback."""

    return STEP_TITLES.get(step, f"Step {_format_step_number(step)}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments for the renderer CLI."""

    parser = argparse.ArgumentParser(description="Render the workshop README.")
    parser.add_argument("--step", type=int, required=True, help="Step number to render.")
    parser.add_argument("--owner", help="GitHub repository owner.")
    parser.add_argument("--repo", help="GitHub repository name.")
    parser.add_argument(
        "--terminal-step",
        type=int,
        default=TERMINAL_STEP,
        help=f"Last numbered workshop step before cleanup (default: {TERMINAL_STEP}).",
    )
    parser.add_argument("--out", type=Path, help="Optional file to write instead of stdout.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    detected_owner, detected_repo = _detect_owner_repo()
    owner = args.owner if args.owner is not None else detected_owner
    repo = args.repo if args.repo is not None else detected_repo

    rendered = render(
        args.step,
        owner=owner,
        repo=repo,
        terminal_step=args.terminal_step,
    )
    if args.out is not None:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
