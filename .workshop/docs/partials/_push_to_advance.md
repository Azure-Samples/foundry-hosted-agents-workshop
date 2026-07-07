---

<!-- workshop-footer: push-to-advance -->
<a id="advance"></a>

## ✅ Done with this step? Push to advance.

**Next:** Step {{NEXT_STEP_NUMBER}} — {{NEXT_STEP_TITLE}}

Commit the files you created or edited in this step and push them to `main`. The push automatically loads Step {{NEXT_STEP_NUMBER}} — there is no button to click.

```bash
git add -A
git commit -m "Complete step {{CURRENT_STEP}}"
git push
```

After the **Advance workshop on push to main** Action finishes, run **`git pull`** to refresh your `README.md` and the next step's files. If you're reading on GitHub, refresh the page.

> Each push to `main` advances the workshop by exactly **one** step, so push once — when this step is done.

> **Prefer to stay local?** Run `python .workshop/scripts/advance_step.py --expected-current-step {{CURRENT_STEP}} --auto-commit` (or `make advance`) instead. That advances locally and records it in the same commit, so your next push won't advance again. See [Working fully locally](.workshop/docs/steps/00-intro.md#5-working-fully-locally-no-github-actions).

<sub>Made a mistake? Use the [Reset workshop](https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/reset-workshop.yml) workflow, or run `python .workshop/scripts/advance_step.py --reset --auto-commit` locally.</sub>
