# Workshop authoring material

You are looking at the **workshop machinery**, not the thing you build. If you
are a participant, you almost never need to touch anything here — your work
happens in [`travel_assistant/`](../travel_assistant/) at the repo root, and
your current step is rendered into the root [`README.md`](../README.md).

This hidden folder keeps the workshop's authoring material out of your way:

- `docs/` — step docs (`steps/NN-*.md`), partials, and assets. These render
  into the root `README.md`.
- `step_files/` — the starter files each step lays down onto `travel_assistant/`.
- `solutions/` — the complete, runnable reference solution for each step.
- `scripts/` — the advance/init/reset engine, the README renderer, the step
  linter, and the test suite.

Per-instance runtime state (the workshop's current step and backups) lives in a
separate sibling folder, [`../.workshop_instance/`](../.workshop_instance/); the
advance engine writes and commits it for you.

## Running the loop

Run everything from the **repo root**:

```bash
make advance                                   # or: python .workshop/scripts/advance_step.py --auto-commit
make reset                                     # or: python .workshop/scripts/advance_step.py --reset --auto-commit
make preflight                                 # or: python .workshop/scripts/preflight.py
```

## Maintaining the workshop

If you are editing the workshop itself, follow
[`../.github/instructions/workshop.instructions.md`](../.github/instructions/workshop.instructions.md)
and [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
