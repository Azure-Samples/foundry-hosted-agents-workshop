# Contributing

Thanks for helping improve this workshop. This is a **workshop template repository**: most people should click **Use this template** and create their own copy instead of contributing changes upstream.

## Reporting issues

We always welcome bug reports, suggestions, and overall feedback. If you find a bug, have a question, or want to suggest an improvement, please [open an issue](https://github.com/Azure-Samples/foundry-hosted-agents-workshop/issues/new) on this repository. We welcome feedback from everyone!

Before filing a new issue, please search the [existing issues](https://github.com/Azure-Samples/foundry-hosted-agents-workshop/issues) to make sure it doesn't already exist. If you find one that matches, add your feedback to the discussion and consider upvoting it (👍) so we can prioritize popular requests.

A good bug report makes it much easier to reproduce and fix the problem. Where relevant, please include:

- A high-level description of the problem.
- The step you were on and a _minimal reproduction_ — the smallest set of actions, code, or configuration needed to trigger the wrong behavior.
- The _expected behavior_ contrasted with the _actual behavior_ you observed.
- Your environment: OS, Python version, and how you're running the workshop (Codespaces, dev container, or local).

## Improving the workshop

The workshop README is rendered from `.workshop/docs/steps/NN-*.md`. Advancing rewrites `README.md`, copies the next starter files from `.workshop/step_files/NN/`, and updates the committed workshop state. Step 0 advances via the manual **Start the workshop** action (`start-workshop.yml`); from Step 1 on, pushing to `main` triggers the **Advance workshop on push to main** workflow (`advance-on-push.yml`). See the Step 0 troubleshooting guidance in [`.workshop/docs/steps/00-intro.md`](.workshop/docs/steps/00-intro.md#troubleshooting) for participant-facing failure modes.

Advancing a step **overlays** `.workshop/step_files/NN/` onto the participant's working folder and never deletes anything, so each step only needs to ship what is genuinely new. Keep these invariants true:

> `.workshop/step_files/NN/` contains **only the new files that step `NN` introduces** (starters/stubs with TODOs), plus its `STEP_README.md`. Files that an earlier step already delivered and that step `NN` merely *modifies* (for example `main.py` or `agent.manifest.yaml`) are edited in place by the participant following the step doc — they are **not** re-shipped, so the overlay never clobbers the participant's earlier work.

> `.workshop/solutions/NN-*/` is the **complete, runnable** state at the end of step `NN`, and it must match what the step doc teaches. Never minify code in either `.workshop/step_files/` or `.workshop/solutions/` — keep it in the clean, expanded teaching style.

The step docs under `.workshop/docs/steps/` are the source of truth for code content: `.workshop/docs/steps/*.md` > `.workshop/solutions/` > `.workshop/step_files/`. For example, step 2 introduces a single new file, `tools.py`; the participant edits their existing `main.py` in place to register the tools, so `.workshop/step_files/02/` ships only `tools.py` (plus `STEP_README.md`), while `.workshop/solutions/02-tools/` contains the full working agent including `tools.py`.

Other conventions:

- `.env` is defined once in step 0; do not add `.env.example` to any later `.workshop/step_files/NN`.
- `requirements.txt` is forward-loaded in `.workshop/step_files/00/` and carried by the overlay; do not add it to later `.workshop/step_files/NN`.
- The model is pre-deployed in step 0 and selected at runtime via `AZURE_AI_MODEL_DEPLOYMENT_NAME`, so agent manifests use `resources: []` (only declare non-model resources such as a toolbox when a step actually adds one).

## Adding a new step

1. Create a new step document at `.workshop/docs/steps/NN-name.md`.
2. Create the matching starter files in `.workshop/step_files/NN/`.
3. Create the completed reference solution in `.workshop/solutions/NN-name/`.
4. Update any previous/next navigation text and shared partials as needed.
5. Bump the terminal step in the renderer/advance system so the new final step is reachable.
6. Run the step lint and smoke-test advancing through the affected steps on a fork.

## Suggested workflow

We use and recommend the following workflow:

1. **Start with an issue.** Open one (or reuse an existing one) so we can agree on the direction before you invest time. Skip this only for trivial fixes such as typos. Please state on the issue that you're taking it on.
2. **Fork and branch.** Create a personal fork, then branch off `main` with a clear name (for example `issue-123` or `fix-step-04-link`).
3. **Make focused changes.** Keep each PR scoped to one topic — don't surprise us with a large, unrelated change. When touching `.workshop/step_files/`, keep the matching `.workshop/solutions/` in sync, and follow the existing teaching style.
4. **Test locally.** Run `python .workshop/scripts/lint_steps.py` and the unit tests (`python -m pytest .workshop/scripts/tests`), and smoke-test advancing through any affected steps on a fork.
5. **Open a PR** against `main`, describe the issue it addresses, and wait for CI and maintainer review.

## Pull request checklist

- [ ] Ran `python .workshop/scripts/render_readme.py --step <N>` for each step this PR touches and visually inspected the output
- [ ] Smoke-tested the advance workflow locally on a fork
- [ ] Ran the unit tests (`python -m pytest .workshop/scripts/tests`)
- [ ] Added or updated tests where possible
- [ ] Updated any affected `.workshop/docs/steps/` content
- [ ] If touching `.workshop/step_files/`, also updated the matching `.workshop/solutions/`
- [ ] Reverted any local `README.md` / `.workshop_instance/` changes from rendering or advance smoke-testing (the workshop regenerates them)
- [ ] Linked to an issue, and no other open PR targets it
- [ ] No secrets committed

## Contributor License Agreement

This project requires a Contributor License Agreement (CLA). When you submit a pull request, a CLA bot will check whether you need to sign one and guide you through the process. You only need to do this once across all Microsoft repos. For details, visit <https://cla.opensource.microsoft.com>.

## Code of conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information, see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com).

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
