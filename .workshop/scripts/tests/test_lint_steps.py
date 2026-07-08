from pathlib import Path

import pytest

import scripts.lint_steps as lint_steps


@pytest.fixture
def synthetic_repo(tmp_path, monkeypatch):
    """Build a minimal repo layout and point the linter at it.

    Mirrors the real ``.workshop`` structure so source-relative links resolve
    against the same base paths the renderer uses.
    """

    repo = tmp_path
    steps = repo / ".workshop" / "docs" / "steps"
    assets = repo / ".workshop" / "docs" / "assets"
    solution = repo / ".workshop" / "solutions" / "01-basic"
    steps.mkdir(parents=True)
    assets.mkdir(parents=True)
    solution.mkdir(parents=True)
    (assets / "01-shot.png").write_bytes(b"\x89PNG")

    monkeypatch.setattr(lint_steps, "REPO_ROOT", repo)
    monkeypatch.setattr(lint_steps, "WORKSHOP_DIR", repo / ".workshop")
    return steps


def _write(steps: Path, body: str) -> None:
    (steps / "01-basic.md").write_text(body, encoding="utf-8")


def test_source_relative_links_pass(synthetic_repo):
    _write(
        synthetic_repo,
        "See [`solution`](../../solutions/01-basic/) "
        "![shot](../assets/01-shot.png) "
        "[self](01-basic.md#top) "
        "[web](https://example.com) [anchor](#top)\n",
    )
    assert lint_steps._source_link_failures() == []


def test_root_relative_link_is_rejected(synthetic_repo):
    _write(synthetic_repo, "[solution](.workshop/solutions/01-basic/)\n")

    failures = lint_steps._source_link_failures()

    assert len(failures) == 1
    assert "root-relative link" in failures[0]
    assert ".workshop/solutions/01-basic/" in failures[0]


def test_broken_image_is_rejected(synthetic_repo):
    _write(synthetic_repo, "![missing](../assets/does-not-exist.png)\n")

    failures = lint_steps._source_link_failures()

    assert len(failures) == 1
    assert "broken link" in failures[0]
    assert "does-not-exist.png" in failures[0]


def test_absolute_and_anchor_links_are_ignored(synthetic_repo):
    _write(
        synthetic_repo,
        "[web](https://example.com/x) [mail](mailto:a@b.com) [anchor](#section)\n",
    )
    assert lint_steps._source_link_failures() == []


def test_real_docs_have_no_link_failures():
    """The shipped step docs and partials must satisfy check K."""
    assert lint_steps._source_link_failures() == []
