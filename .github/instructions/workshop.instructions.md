---
applyTo: ".workshop/**"
description: "Authoring rules for the workshop material under .workshop/."
---

# Workshop authoring instructions

These rules apply whenever you edit workshop **authoring material** under
`.workshop/**` — the step docs, `step_files/`, `solutions/`, and the advance
engine in `scripts/`. They are for **workshop maintainers**, not for
participants building `travel_assistant/`. The guiding principles and the
review checklist in [`../copilot-instructions.md`](../copilot-instructions.md)
still apply on top of everything here.

## Where things live

- `.workshop/docs/` — step docs (`steps/NN-*.md`), partials, and assets. Step
  docs render into the participant's root `README.md`.
- `.workshop/step_files/NN/` — the starter files step `NN` lays down.
- `.workshop/solutions/NN-*/` — the complete, runnable end state of step `NN`.
- `.workshop/scripts/` — the advance/init/reset engine, the renderer, the
  linter, and the test suite (`tests/`).
- `.workshop_instance/` — per-instance runtime state (`.workshop-state.json`,
  `workshop_backups/`, `.workshop-initialized`). The engine writes and commits
  it; don't hand-edit it.

Invoke the scripts from the **repo root**, e.g.
`python .workshop/scripts/advance_step.py`, `python .workshop/scripts/lint_steps.py`,
and `python -m pytest .workshop/scripts/tests`.

## Conventions

- **Overlay model.** Advancing a step overlays `.workshop/step_files/NN/` onto
  the participant's `travel_assistant/` folder and never deletes anything.
  `.workshop/step_files/NN/` ships **only the new files step `NN` introduces**
  (starters/stubs with TODOs, plus its `STEP_README.md`). Files an earlier step
  delivered and this step merely edits are changed in place by the learner via
  the step doc — do not re-ship them, so the overlay never clobbers earlier work.
- **`.workshop/solutions/NN-*/` is the complete, runnable end state** of step
  `NN` and must match what the step doc teaches.
- **Source-of-truth order:** `.workshop/docs/steps/*.md` > `.workshop/solutions/`
  > `.workshop/step_files/`. Change one, update the others so they agree —
  including code embedded in the step docs.
- **`.env.example` and `requirements.txt`** are defined once in step 0; do not
  add them to later `.workshop/step_files/NN`.
- **Keep it teaching-quality.** Never minify code in `.workshop/step_files/` or
  `.workshop/solutions/` — keep it clean and expanded (KISS).
- **Links and images in step docs are repo-root-relative.** Step docs render
  into the participant's **root** `README.md`, so author relative links and
  image paths as they resolve *from the repo root* — e.g.
  `.workshop/docs/assets/NN-*.png` and `.workshop/solutions/NN-*/`, not
  `docs/assets/...` or `../assets/...`. Consequence: viewing a source step doc
  directly on GitHub (under `.workshop/docs/steps/`) shows those relative links
  as broken — that is expected; the **rendered `README.md` is the canonical
  artifact**. For links that must leave the docs, use absolute URLs:
  `https://github.com/{{OWNER}}/{{REPO}}/...` when the link must target the
  participant's **own** instance (e.g. their Actions workflows), and a hardcoded
  `https://github.com/Azure-Samples/foundry-hosted-agents-workshop/...` when it must reach
  the **template/upstream** repo (e.g. the workshop-feedback issue template).

## Before you open a PR

- Run `python .workshop/scripts/render_readme.py --step <N>` for each step this
  PR touches (use `--step 0` if you changed shared partials/header/footer) and
  visually inspect the output. The renderer prints to **stdout** by default —
  don't redirect it into `README.md`.
- Smoke-test the advance workflow locally on a fork.
- Run `python -m pytest .workshop/scripts/tests` and keep it green; add or
  update tests where possible.
- If you touched `.workshop/step_files/`, update the matching
  `.workshop/solutions/` (and the step doc) so they stay in sync.
- Clean up afterward: rendering to `README.md` or smoke-testing the advance
  workflow rewrites the participant-facing `README.md` and `.workshop_instance/`
  state. Revert those before committing — the running workshop regenerates them,
  so they must not land in your PR.
