"""Validate workshop step metadata and rendered README output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).parent))

from render_readme import FINAL_STEP, STEP_TITLES, parse_step_marker, render

SCRIPT_DIR = Path(__file__).parent
# This script lives at .workshop/scripts/, so the repo root is two levels up and
# the workshop authoring material (docs, step_files, solutions) is one level up.
REPO_ROOT = SCRIPT_DIR.resolve().parents[1]
WORKSHOP_DIR = SCRIPT_DIR.resolve().parent
STEP_FILE_RE = re.compile(r"^(?P<step>\d{2})-.+\.md$")
NUMERIC_DIR_RE = re.compile(r"^\d+$")
START_WORKFLOW_URL_PART = "actions/workflows/start-workshop.yml"
LEGACY_ADVANCE_URL_PART = "actions/workflows/advance-step.yml"
START_FOOTER_MARKER = "<!-- workshop-footer: start-workshop -->"
PUSH_FOOTER_MARKER = "<!-- workshop-footer: push-to-advance -->"
OWNER_PLACEHOLDER = "{{OWNER}}"
REPO_PLACEHOLDER = "{{REPO}}"
GITHUB_URL = "https://github.com/"


@dataclass
class CheckResult:
    """A single lint invariant result."""

    id: str
    description: str
    status: str
    details: list[str]


def _format_step(step: int) -> str:
    return f"{step:02d}"


def _format_step_list(steps: Sequence[int]) -> str:
    return ", ".join(_format_step(step) for step in steps)


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _discover_step_files() -> dict[int, list[Path]]:
    steps_dir = WORKSHOP_DIR / "docs" / "steps"
    files_by_step: dict[int, list[Path]] = {}
    if not steps_dir.exists():
        return files_by_step

    for path in sorted(steps_dir.glob("*.md")):
        match = STEP_FILE_RE.match(path.name)
        if match is None:
            continue
        step = int(match.group("step"))
        files_by_step.setdefault(step, []).append(path)
    return files_by_step


def _load_current_step() -> tuple[int | None, list[str]]:
    state_path = REPO_ROOT / ".workshop_instance" / ".workshop-state.json"
    if not state_path.exists():
        return None, [".workshop-state.json is missing"]

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f".workshop-state.json is invalid JSON: {exc}"]

    if not isinstance(state, dict):
        return None, [".workshop-state.json must contain a JSON object"]

    current_step = state.get("current_step")
    if type(current_step) is not int:
        return None, [".workshop-state.json current_step must be an integer"]

    return current_step, []


def _render_steps(step_files: dict[int, list[Path]]) -> tuple[dict[int, str], list[str]]:
    rendered: dict[int, str] = {}
    failures: list[str] = []

    for step in sorted(step_files):
        try:
            rendered[step] = render(step, owner="lint", repo="lint")
        except Exception as exc:  # noqa: BLE001 - stdlib script, report any renderer failure.
            failures.append(f"step {_format_step(step)}: {type(exc).__name__}: {exc}")

    return rendered, failures


def _step_file_solution_warnings() -> list[str]:
    warnings: list[str] = []
    step_files_dir = WORKSHOP_DIR / "step_files"
    solutions_dir = WORKSHOP_DIR / "solutions"
    if not step_files_dir.exists():
        return warnings

    for path in sorted(step_files_dir.iterdir()):
        if not path.is_dir() or NUMERIC_DIR_RE.match(path.name) is None:
            continue

        step = int(path.name)
        if step <= 0:
            continue

        solution_step = step - 1
        if not list(solutions_dir.glob(f"{solution_step:02d}-*")):
            warnings.append(
                f"⚠️ step_files/{_format_step(step)} exists but no matching "
                f"solutions/{_format_step(solution_step)}"
            )

    return warnings


def _scan_stray_placeholders() -> list[str]:
    issues: list[str] = []
    paths: list[Path] = []
    docs_dir = WORKSHOP_DIR / "docs"
    if docs_dir.exists():
        paths.extend(path for path in docs_dir.rglob("*") if path.is_file())

    readme = REPO_ROOT / "README.md"
    if readme.exists():
        paths.append(readme)

    for path in sorted(paths):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            # Binary assets (e.g. images) can't hold text placeholders; skip them.
            continue

        for line_number, line in enumerate(lines, start=1):
            has_placeholder = OWNER_PLACEHOLDER in line or REPO_PLACEHOLDER in line
            if has_placeholder and GITHUB_URL not in line:
                issues.append(f"{_relative(path)}:{line_number}")

    return issues


def _add_result(
    results: list[CheckResult],
    check_id: str,
    description: str,
    failures: list[str],
    pass_detail: str,
) -> None:
    status = "fail" if failures else "pass"
    results.append(
        CheckResult(
            id=check_id,
            description=description,
            status=status,
            details=failures if failures else [pass_detail],
        )
    )


def run_checks() -> tuple[list[CheckResult], list[str]]:
    """Run all lint invariants and return results plus non-fatal warnings."""

    results: list[CheckResult] = []
    warnings = _step_file_solution_warnings()

    step_files = _discover_step_files()
    valid_steps = set(STEP_TITLES)

    missing_steps = sorted(step for step in valid_steps if step not in step_files)
    title_failures = ["STEP_TITLES is missing step 99"] if FINAL_STEP not in valid_steps else []
    title_failures.extend(
        [f"missing docs/steps/{_format_step(step)}-*.md" for step in missing_steps]
    )
    _add_result(
        results,
        "A",
        "Known steps have docs/steps files",
        title_failures,
        f"found files for steps {_format_step_list(sorted(valid_steps))}",
    )

    unknown_steps = sorted(step for step in step_files if step not in valid_steps)
    _add_result(
        results,
        "B",
        "No docs/steps files for unknown steps",
        [f"unknown step {_format_step(step)}" for step in unknown_steps],
        "all docs/steps files map to STEP_TITLES",
    )

    current_step, state_errors = _load_current_step()
    state_failures = list(state_errors)
    if current_step is not None and current_step not in valid_steps:
        state_failures.append(
            f"current_step {_format_step(current_step)} is not in STEP_TITLES"
        )
    state_pass_detail = (
        f"current_step is {_format_step(current_step)}"
        if current_step is not None
        else "current_step could not be read"
    )
    _add_result(
        results,
        "C",
        ".workshop-state.json current_step is valid",
        state_failures,
        state_pass_detail,
    )

    rendered, render_failures = _render_steps(step_files)
    _add_result(
        results,
        "D",
        "Renderer can render every step file",
        render_failures,
        f"rendered {len(rendered)} step(s)",
    )

    marker_failures: list[str] = []
    for step, output in sorted(rendered.items()):
        marker = parse_step_marker(output)
        if marker != step:
            found = "none" if marker is None else _format_step(marker)
            marker_failures.append(
                f"step {_format_step(step)} marker mismatch: found {found}"
            )
    _add_result(
        results,
        "E",
        "Rendered outputs contain matching step markers",
        marker_failures,
        f"checked {len(rendered)} rendered marker(s)",
    )

    footer_failures: list[str] = []
    for step, output in sorted(rendered.items()):
        if step == FINAL_STEP:
            continue
        if step == 0:
            if START_FOOTER_MARKER not in output:
                footer_failures.append(f"step 00 missing {START_FOOTER_MARKER}")
            if START_WORKFLOW_URL_PART not in output:
                footer_failures.append(f"step 00 missing {START_WORKFLOW_URL_PART}")
        else:
            if PUSH_FOOTER_MARKER not in output:
                footer_failures.append(
                    f"step {_format_step(step)} missing {PUSH_FOOTER_MARKER}"
                )
            if START_WORKFLOW_URL_PART in output:
                footer_failures.append(
                    f"step {_format_step(step)} should not link {START_WORKFLOW_URL_PART}"
                )
            if LEGACY_ADVANCE_URL_PART in output:
                footer_failures.append(
                    f"step {_format_step(step)} still links removed {LEGACY_ADVANCE_URL_PART}"
                )
    _add_result(
        results,
        "F",
        "Advance footer matches each step's mechanism (Setup=button, later=push)",
        footer_failures,
        "Setup links Start-the-workshop; later steps use the push-to-advance footer",
    )

    final_failures: list[str] = []
    final_output = rendered.get(FINAL_STEP)
    if final_output is not None:
        for token in (
            START_FOOTER_MARKER,
            PUSH_FOOTER_MARKER,
            START_WORKFLOW_URL_PART,
            LEGACY_ADVANCE_URL_PART,
        ):
            if token in final_output:
                final_failures.append(
                    f"step {_format_step(FINAL_STEP)} contains {token}"
                )
    _add_result(
        results,
        "G",
        "Final step omits any advance footer",
        final_failures,
        "step 99 has no advance footer",
    )

    _add_result(
        results,
        "H",
        "step_files directories have previous-step solutions",
        [],
        f"{len(warnings)} warning(s) detected",
    )

    readme = REPO_ROOT / "README.md"
    readme_failures: list[str] = []
    if not readme.exists():
        readme_failures.append("README.md is missing")
    elif current_step is None:
        readme_failures.append("cannot compare README.md marker because current_step is unavailable")
    else:
        marker = parse_step_marker(readme.read_text(encoding="utf-8"))
        if marker != current_step:
            found = "none" if marker is None else _format_step(marker)
            readme_failures.append(
                f"README.md marker {found} does not match state {_format_step(current_step)}"
            )
    _add_result(
        results,
        "I",
        "README.md marker matches workshop state",
        readme_failures,
        "README.md marker matches .workshop-state.json",
    )

    stray_placeholders = _scan_stray_placeholders()
    _add_result(
        results,
        "J",
        "No stray {{OWNER}} or {{REPO}} placeholders",
        [f"stray placeholder at {issue}" for issue in stray_placeholders],
        "no stray owner/repo placeholders outside GitHub URLs",
    )

    return results, warnings


def _print_human(results: list[CheckResult], warnings: list[str]) -> None:
    def can_encode(text: str) -> bool:
        encoding = sys.stdout.encoding or "utf-8"
        try:
            text.encode(encoding)
        except UnicodeEncodeError:
            return False
        return True

    use_icons = can_encode("✅❌⚠️")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(warning if use_icons else warning.replace("⚠️ ", "WARNING: "))
        print()

    rows = []
    for result in results:
        icon = ("✅" if result.status == "pass" else "❌") if use_icons else result.status.upper()
        rows.append((icon, result.id, result.description, "; ".join(result.details)))

    headers = ("Status", "Check", "Invariant", "Details")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fmt(row: Sequence[str]) -> str:
        return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))

    print(fmt(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(fmt(row))

    failure_count = sum(1 for result in results if result.status == "fail")
    warning_count = len(warnings)
    summary_icon = ("✅" if failure_count == 0 else "❌") if use_icons else ("OK" if failure_count == 0 else "FAIL")
    print(f"\n{summary_icon} {failure_count} failure(s), {warning_count} warning(s)")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint workshop step consistency.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a human table.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    results, warnings = run_checks()
    has_failures = any(result.status == "fail" for result in results)

    if args.json:
        payload = {
            "ok": not has_failures,
            "results": [asdict(result) for result in results],
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2))
    else:
        _print_human(results, warnings)

    return 1 if has_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
