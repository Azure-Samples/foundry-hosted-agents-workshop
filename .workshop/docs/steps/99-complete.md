# 🎉 You did it

Congratulations — you built **TravelBuddy** from a single hosted agent into a
multi-agent, workflow-orchestrated, memory-bearing travel assistant grounded in
your own data.

You started with a simple chat loop.

You ended with an assistant that can use tools, consult documentation, analyze
files, retrieve destination knowledge, delegate to specialists, run a durable
workflow, pause for approval, and remember traveler preferences over time.

That is a lot of ground to cover.

Take a breath.

Then clean up the cloud resources you created.

## Recap: the journey

| Step | What TravelBuddy gained |
| --- | --- |
| Step 1 | A first hosted agent named **TravelBuddy**. |
| Step 2 | Function tools for weather, local time, and currency conversion. |
| Step 3 | MCP live flight search with OctoTrip Flights. |
| Step 4 | Foundry Toolbox capabilities: CSV analysis with Code Interpreter, plus web search. |
| Step 5 | Retrieval-augmented generation over a destinations index. |
| Step 6 | A local travel-guide skill (colorful PDF trip guides) plus a required Foundry skill for shared, Responsible-AI response guardrails. |
| Step 7 | A multi-agent runtime handoff between a Coordinator and specialist agents. |
| Step 8 | A durable workflow with checkpoints and an approval gate. |
| Step 9 | Long-term memory for traveler preferences across sessions. |

Along the way, you practiced a pattern that applies far beyond travel:

- Start with one clear assistant persona.
- Add tools when the model needs to act.
- Add grounding when the model needs trusted knowledge.
- Add file and code tools when the model needs to inspect user data.
- Add retrieval when the assistant needs a private knowledge base.
- Add skills when behavior should be packaged and reused.
- Add multiple agents when specialist roles make the system easier to reason about.
- Add workflows when the process must be observable, repeatable, or approval-driven.
- Add memory when the assistant should improve across sessions.

That progression is the heart of the workshop.

## Where to take it next

You now have a working foundation.

Here are a few useful next experiments.

### Replace a mock tool with a real API

The weather, time, and currency tools were intentionally small so the workshop
could stay focused on the agent architecture.

Try swapping one mock for a production API.

For example:

- Connect `get_weather` to a real weather provider.
- Connect `convert_currency` to a live exchange-rate service.
- Add rate limits, retries, and friendly error handling.
- Log tool inputs and outputs so you can debug real-world failures.

That is often the first step from workshop code to a usable assistant.

### Add evaluation runs

Create a small set of travel scenarios and evaluate TravelBuddy against them.

Good evaluation prompts might include:

- A family trip with budget constraints.
- A business trip with tight arrival windows.
- A traveler with accessibility requirements.
- A trip that should trigger an approval gate.
- A returning user whose saved preferences should be remembered.

Use the Azure AI Foundry evaluation tooling to measure quality, groundedness,
relevance, tool use, and regressions over time:

- [Evaluate with Azure AI Foundry](https://learn.microsoft.com/azure/ai-foundry/how-to/develop/evaluate-sdk)

Treat evaluation as part of the application, not as an afterthought.

### Deploy to a chat surface

A command-line assistant is great for learning.

A real assistant usually lives where users already work.

Possible next surfaces include:

- A Teams app.
- A web chat embed.
- An internal support portal.
- A workflow-triggered assistant in an operations dashboard.

Useful starting points:

- [Microsoft Teams AI samples](https://github.com/microsoft/teams-ai)
- [Microsoft Foundry samples](https://github.com/microsoft-foundry/foundry-samples)

When you move to a chat surface, keep the same boundaries you practiced here:

- Keep secrets out of source control.
- Keep tool behavior explicit.
- Keep approval gates visible.
- Keep telemetry and evaluation in the loop.
- Keep cleanup scripts ready for temporary environments.

### Try a different domain

Replace **travel** with your own scenario.

The bones generalize.

For example:

- Travel itinerary planning becomes sales account planning.
- Destination retrieval becomes product documentation retrieval.
- Weather and currency tools become inventory and pricing tools.
- The travel-guide skill becomes a proposal-generation skill.
- The Coordinator and specialists become planner, researcher, reviewer, and writer agents.
- The approval gate becomes legal, finance, or manager review.
- Traveler memory becomes customer, employee, or project preference memory.

The important part is not the travel theme.

The important part is the shape of the system.

## 🚨 Cleanup: do this before you walk away

Workshop resources can cost money if you leave them running. TravelBuddy's
resources come from two places, and a complete teardown covers both:

1. **azd-provisioned hosting** — the hosted-agent container app and anything
   `azd` created. Remove these with `azd down`.
2. **Out-of-band Foundry resources** — the **Toolbox** (Step 4), the **Azure AI
   Search index** (Step 5), and the **Memory Store** (Step 9) were created by
   helper scripts, not by `azd provision`, so `azd down` does **not** remove
   them. Use `.workshop/scripts/cleanup.py` for those.

Checklist:

- [ ] Tear down the azd-provisioned hosting in one shot:

  <!-- terminal -->
  ```bash
  azd down --force --purge
  ```

- [ ] Dry-run the out-of-band cleanup and confirm it only lists resources
  prefixed with `WORKSHOP_RESOURCE_PREFIX`:

  <!-- terminal -->
  ```bash
  python .workshop/scripts/cleanup.py
  ```

- [ ] Delete the listed out-of-band resources (toolbox, Search index, memory store):

  <!-- terminal -->
  ```bash
  python .workshop/scripts/cleanup.py --apply
  ```

  Or skip the confirmation prompt:

  <!-- terminal -->
  ```bash
  python .workshop/scripts/cleanup.py --apply --yes
  ```

- [ ] Deactivate and remove your local Python virtual environment:

  <!-- terminal -->
  ```bash
  deactivate              # exit the venv if it is still active
  rm -rf .venv            # macOS / Linux
  ```

  Or, on Windows (PowerShell):

  <!-- terminal -->
  ```powershell
  deactivate              # only if a venv is currently active
  Remove-Item -Recurse -Force .venv
  ```

- [ ] Optional: delete this repository if you do not want to keep your workshop copy.

> ⚠️ `cleanup.py` only touches resources whose names match your
> `WORKSHOP_RESOURCE_PREFIX`. That is why Steps 5 and 9 asked you to keep the
> Search index and `MEMORY_STORE_NAME` prefixed. Anything you renamed manually
> will not be cleaned up by the script — list those in the Azure portal and
> delete them by hand.

Before running `--apply`, double-check:

- The active Azure subscription is the one you used for the workshop.
- Your `.env` still contains the correct `WORKSHOP_RESOURCE_PREFIX`.
- The dry run output does not include resources you want to keep.
- Any manually renamed resources are recorded somewhere so you can remove them.

After cleanup, it is a good idea to open the Azure portal and verify that the
resource group or individual workshop resources are gone.

## Feedback

> Please open an issue with the
> [workshop-feedback template](https://github.com/Azure-Samples/foundry-hosted-agents-workshop/issues/new?template=workshop-feedback.md) —
> what was confusing, what was great, what would you change?

Helpful feedback includes:

- Which step took the longest?
- Which concept clicked immediately?
- Which concept needed more explanation?
- Did any command fail on your machine?
- Were the troubleshooting notes enough?
- What would make this better for the next participant?

## Credits

- Built on top of the upstream
  [foundry-samples](https://github.com/microsoft-foundry/foundry-samples) —
  thanks to that team.

Thank you for spending time with the workshop.

Thank you for helping make agent development more approachable.

## Reset

> Want to redo the workshop? Run the **Reset workshop** GitHub Action
> (Actions tab → Reset workshop → Run workflow), **or** run it locally with
> `python .workshop/scripts/advance_step.py --reset --auto-commit` (or `make reset`).
> Either way, you'll be back at step 0.

If you reset, the repository returns to the beginning of the guided flow.

That is useful when you want to:

- Run the workshop again from a clean state.
- Demo the workshop to someone else.
- Validate that the steps still work after template changes.
- Compare your completed app with the reference solutions.

Have fun building the next assistant.
