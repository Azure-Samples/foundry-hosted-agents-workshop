---

<!-- workshop-footer: start-workshop -->
<a id="advance"></a>

[![▶ Start the workshop](https://img.shields.io/badge/%E2%96%B6_Start_the_workshop-Step_{{NEXT_STEP_NUMBER}}-2ea44f?style=for-the-badge)](https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/start-workshop.yml)

**Next:** Step {{NEXT_STEP_NUMBER}} — {{NEXT_STEP_TITLE}}

Click the badge to open **Start the workshop**, then click **Run workflow**. It moves you from Setup to Step {{NEXT_STEP_NUMBER}}. Pull after the action completes.

Or open **Actions → Start the workshop → Run workflow** manually.

From Step {{NEXT_STEP_NUMBER}} onward you don't click a button to advance — you just **commit your work and push to `main`**, and the next step loads automatically.

> 💡 **Button returns 404?** Your repo's one-time **Initialize workshop** Action hasn't run yet. Open the **Actions** tab, run **Initialize workshop → Run workflow**, then refresh this page.

> **Prefer to stay local?** Run `python .workshop/scripts/advance_step.py --expected-current-step {{CURRENT_STEP}} --auto-commit` (or `make advance`) instead of clicking the button. See [Working fully locally](.workshop/docs/steps/00-intro.md#5-working-fully-locally-no-github-actions) for the full local flow.

<sub>Made a mistake? Use the [Reset workshop](https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/reset-workshop.yml) workflow, or run `python .workshop/scripts/advance_step.py --reset --auto-commit` locally.</sub>
