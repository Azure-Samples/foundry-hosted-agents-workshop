from pathlib import Path

import pytest

import scripts.render_readme as render_readme


def _write_step(directory: Path, step: int, slug: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{step:02d}-{slug}.md").write_text(body, encoding="utf-8")


@pytest.fixture
def synthetic_steps(tmp_path, monkeypatch):
    steps_dir = tmp_path / "steps"
    _write_step(steps_dir, 0, "intro", "<!-- step: 0 -->\n\n# Synthetic setup body\n")
    _write_step(steps_dir, 4, "half", "<!-- step: 4 -->\n\n# Synthetic half body\n")
    _write_step(steps_dir, 9, "memory", "<!-- step: 9 -->\n\n# Synthetic terminal body\n")
    _write_step(steps_dir, 99, "complete", "<!-- step: 99 -->\n\n# Synthetic cleanup body\n")
    monkeypatch.setattr(render_readme, "STEPS_DIR", steps_dir)
    return steps_dir


def test_parse_step_marker_returns_integer():
    assert render_readme.parse_step_marker("before\n<!-- step: 3 -->\nafter") == 3


def test_parse_step_marker_returns_none_when_absent():
    assert render_readme.parse_step_marker("# no marker here") is None


def test_render_step_zero_includes_step_file_body(synthetic_steps):
    output = render_readme.render(0, owner="foo", repo="bar")

    assert "# Synthetic setup body" in output


def test_render_step_zero_includes_header_and_footer(synthetic_steps):
    output = render_readme.render(0, owner="foo", repo="bar")

    assert "Foundry hosted agents with Agent Framework" in output
    assert "Start the workshop" in output
    assert "workshop-footer: start-workshop" in output


def test_render_final_step_includes_header_without_next_button(synthetic_steps):
    output = render_readme.render(99, owner="foo", repo="bar")

    assert "Foundry hosted agents with Agent Framework" in output
    assert "# Synthetic cleanup body" in output
    assert "Advance to Step" not in output


def test_render_final_step_shows_step_total_as_99_not_terminal(synthetic_steps):
    """Final cleanup step should read 'Step 99 of 99', not 'Step 99 of 9'."""
    output = render_readme.render(99, owner="foo", repo="bar")

    assert "Step `99` of `99`" in output
    assert "Step `99` of `9`" not in output


def test_render_non_final_step_shows_step_total_as_terminal(synthetic_steps):
    output = render_readme.render(4, owner="foo", repo="bar")

    assert "Step `04` of `9`" in output


def test_progress_bar_boundaries_and_halfway():
    assert render_readme._progress_bar(0, terminal_step=10) == "▱" * 10
    assert render_readme._progress_bar(10, terminal_step=10) == "▰" * 10
    assert render_readme._progress_bar(5, terminal_step=10) == "▰" * 5 + "▱" * 5


def test_render_leaves_no_handlebar_placeholders(synthetic_steps):
    output = render_readme.render(0, owner="foo", repo="bar")

    assert "{{" not in output


def test_owner_repo_substitution_in_next_button_url(synthetic_steps):
    output = render_readme.render(0, owner="foo", repo="bar")

    assert "https://github.com/foo/bar/actions/workflows/start-workshop.yml" in output


def test_render_mid_step_uses_push_to_advance_footer(synthetic_steps):
    """Steps after Setup advance by pushing to main, not a workflow button."""
    output = render_readme.render(4, owner="foo", repo="bar")

    assert "workshop-footer: push-to-advance" in output
    assert "git push" in output
    assert "start-workshop.yml" not in output
    assert "advance-step.yml" not in output


def test_render_includes_local_alternative_command_with_current_step(synthetic_steps):
    """The Advance footer renders a local-flow command tied to the current step."""

    output_zero = render_readme.render(0, owner="foo", repo="bar")
    output_nine = render_readme.render(9, owner="foo", repo="bar")

    assert "--expected-current-step 0" in output_zero
    assert "--auto-commit" in output_zero
    assert "make advance" in output_zero
    assert "--expected-current-step 9" in output_nine
    assert "--auto-commit" in output_nine


def test_render_final_step_omits_local_advance_command(synthetic_steps):
    """The cleanup step has no advance footer at all, including the local flow line."""

    output = render_readme.render(99, owner="foo", repo="bar")

    assert "--expected-current-step" not in output
    assert "make advance" not in output


def test_rendered_output_contains_no_owner_or_repo_placeholders(synthetic_steps):
    """Concrete owner/repo render must not leak literal {{OWNER}} / {{REPO}}."""
    output = render_readme.render(0, owner="octo", repo="demo")

    assert "{{OWNER}}" not in output
    assert "{{REPO}}" not in output
    # Sanity check: the substituted value is actually in the URL.
    assert "github.com/octo/demo/actions/workflows/start-workshop.yml" in output
    assert "github.com/octo/demo/actions/workflows/reset-workshop.yml" in output


def test_step_body_owner_repo_substitution(tmp_path, monkeypatch):
    """{{OWNER}}, {{REPO}}, and {{CURRENT_STEP}} in the step body are substituted."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir(parents=True)
    (steps_dir / "00-intro.md").write_text(
        "<!-- step: 0 -->\n\n"
        "[![Advance](badge)](https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/advance-step.yml"
        "?expected_current_step={{CURRENT_STEP}})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(render_readme, "STEPS_DIR", steps_dir)

    output = render_readme.render(0, owner="alice", repo="proj")

    assert "{{OWNER}}" not in output
    assert "{{REPO}}" not in output
    assert "{{CURRENT_STEP}}" not in output
    assert "github.com/alice/proj/actions/workflows/advance-step.yml?expected_current_step=0" in output


STEPS_BASE = ".workshop/docs/steps"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("../a", True),
        ("00-intro.md", True),
        ("../../solutions/01-basic/", True),
        ("https://example.com/x", False),
        ("http://example.com/x", False),
        ("mailto:a@b.com", False),
        ("//cdn.example.com/x", False),
        ("#section", False),
        ("/etc/hosts", False),
        ("{{OWNER}}/x", False),
    ],
)
def test_is_rebasable_url(url, expected):
    assert render_readme.is_rebasable_url(url) is expected


def test_rebase_relative_link_target():
    text = "[solution](../../solutions/01-basic/)"
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == "[solution](.workshop/solutions/01-basic/)"


def test_rebase_relative_image_target():
    text = "![alt](../assets/01-agent-inspector.png)"
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == "![alt](.workshop/docs/assets/01-agent-inspector.png)"


def test_rebase_preserves_anchor_on_sibling_file():
    text = "[intro](00-intro.md#troubleshooting)"
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == "[intro](.workshop/docs/steps/00-intro.md#troubleshooting)"


def test_rebase_preserves_link_title():
    text = '![alt](../assets/x.png "a caption")'
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == '![alt](.workshop/docs/assets/x.png "a caption")'


def test_rebase_passes_absolute_urls_through():
    text = "[docs](https://example.com/a) and [x](http://example.com/b)"
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == text


def test_rebase_passes_anchor_and_placeholder_through():
    text = "[top](#section) [ci](https://github.com/{{OWNER}}/{{REPO}}/actions)"
    out = render_readme.rebase_relative_links(text, base_dir=STEPS_BASE)
    assert out == text


def test_rebase_from_partials_base():
    text = "[intro](../steps/00-intro.md#troubleshooting)"
    out = render_readme.rebase_relative_links(text, base_dir=".workshop/docs/partials")
    assert out == "[intro](.workshop/docs/steps/00-intro.md#troubleshooting)"


def test_resolve_relative_target_strips_fragment():
    target = render_readme.resolve_relative_target(
        "00-intro.md#troubleshooting", STEPS_BASE
    )
    assert target == ".workshop/docs/steps/00-intro.md"


def test_resolve_relative_target_returns_none_for_non_relative():
    assert render_readme.resolve_relative_target("https://x/y", STEPS_BASE) is None
    assert render_readme.resolve_relative_target("#anchor", STEPS_BASE) is None


def test_render_rebases_source_relative_links_in_body(tmp_path, monkeypatch):
    """Source-relative links in a step body become repo-root-relative in output."""
    steps_dir = tmp_path / "steps"
    steps_dir.mkdir(parents=True)
    (steps_dir / "04-half.md").write_text(
        "<!-- step: 4 -->\n\n"
        "See [`solution`](../../solutions/04-toolbox/) and "
        "![shot](../assets/04-toolbox-toolkit.png).\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(render_readme, "STEPS_DIR", steps_dir)

    output = render_readme.render(4, owner="foo", repo="bar")

    assert "](.workshop/solutions/04-toolbox/)" in output
    assert "](.workshop/docs/assets/04-toolbox-toolkit.png)" in output
    assert "../../solutions" not in output
    assert "](../assets/" not in output

