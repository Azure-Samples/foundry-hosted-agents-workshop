# Foundry hosted agents with Agent Framework — Travel Assistant Workshop

<!-- step: 00 -->

This repository is the **template source** for the workshop. It is not a live Step 00 instance.

After you create your own copy from this template, initialization runs automatically and your copy rewrites `README.md` to the real Step 00 content with progress/status and step navigation.

## What this workshop is about

You will build a **travel assistant** that grows one capability at a time. Starting from a single hosted Foundry agent, by step 9 it will be a multi-agent travel planner with function tools, MCP integration, retrieval-augmented generation, durable workflows, and persistent memory.

The workshop is built on top of the upstream [foundry-samples](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses) and is delivered as a **GitHub template repository**: you don't edit this repo directly — you create your own copy, then advance one step at a time. Each advance rewrites the `README.md` to show the next step in place.

## Create your own repo from this template

This is a **GitHub template repository**. You must create your own copy before doing anything else.

1. Click the green **"Use this template"** button at the top of this page → **Create a new repository**.
2. Pick a name and owner, choose visibility (Public or Private), and click **Create repository**.
3. The included **Initialize workshop** Action runs automatically on the first push to your new repo. It lays down the step 0 starter files into `travel_assistant/` and substitutes your owner/repo into the README's Action URLs. Wait for it to finish in the **Actions** tab of your new repo (\~30 seconds). If you don't see a run, your org may have Actions disabled by default — enable them under **Settings → Actions → General → Allow all actions**, then click **Actions → Initialize workshop → Run workflow** to run it manually. See the **"▶ Start the workshop returns 404"** entry under Troubleshooting below for the full recovery procedure.
4. **Repo settings:** Settings → Actions → General → Workflow permissions → **Read and write permissions**. Most repos inherit this, but org-owned repos may need it set explicitly so the workshop Actions can push.

> 💡 **Already in your own copy?** If the green button at the top reads "Open" rather than "Use this template", you're already in a workshop instance. Continue below.

## How the workshop works

This workshop has one important contract: **`README.md` is the current step**. Each time you advance, the repository rewrites `README.md` so the next set of instructions appears in place.

- **Leaving Setup (Step 0):** click the **▶ Start the workshop** button at the bottom of that step. It opens the workshop's GitHub Action — click **Run workflow** to move to Step 1.
- **Every step after that:** there is no button. When you finish a step, **commit your work and push it to `main`**. The push triggers the **Advance workshop on push to main** Action, which loads the next step (each landed push advances one step).
- After the Action finishes, run **`git pull`** locally. If you are reading in the GitHub UI, refresh the page to see the new `README.md`.
- Advancing lays the next step's canonical starter files **on top of** your `travel_assistant/` directory. Your previous edits are saved to `.workshop_instance/workshop_backups/step-<N>/` in the same commit.

## What you'll build

- Step 1: Chat with a basic hosted `TravelBuddy` agent.
- Step 2: Add function tools for weather, local time, and currency conversion.
- Step 3: Connect an MCP server for external travel documentation.
- Step 4: Use Foundry tools such as Code Interpreter and web search for itinerary analysis.
- Step 5: Ground recommendations with a destinations knowledge base through RAG.
- Step 6: Package reusable itinerary behavior as a skill.
- Step 7: Coordinate flight, hotel, and activities specialists with native multi-agent patterns.
- Step 8 (🧪 experimental): Re-express the same planning flow as a durable workflow with checkpoints.
- Step 9 (🧪 experimental): Remember user preferences across sessions with Foundry Memory.

## When you're ready

Initialization is automatic in your own copy.

- **Using GitHub Actions:** create your repo from this template and push once. The **Initialize workshop** workflow runs automatically.
- **If it didn't run:** open **Actions → Initialize workshop → Run workflow** manually.
- **Working fully locally (no Actions):** run `python .workshop/scripts/advance_step.py --init` from the repository root.

After initialization completes, refresh/pull. The README switches to Step 00 with the **Start the workshop** button; from Step 1 on you advance by pushing to `main`.

## Working fully locally (no GitHub Actions)

You can run the entire workshop loop from your terminal — no browser, no Actions, no `git push` required. Pick whichever flow you prefer; both keep the repository in the same state.

The button in each step's README is just a thin wrapper around `.workshop/scripts/advance_step.py`. Running the script locally does exactly the same file rewrites.

**Advance one step:**

```bash
python .workshop/scripts/advance_step.py --expected-current-step <N> --auto-commit
```

Where `<N>` is the step number you're currently on (the value the button asks for). You can omit `--expected-current-step` locally if you trust the state — the script will print the detected step and advance anyway. The `--auto-commit` flag stages **only** the workshop-owned paths (`README.md`, `.workshop_instance/.workshop-state.json`, `travel_assistant/`, `.workshop_instance/workshop_backups/`) and creates a commit with the same message the Action uses, so unrelated local edits or untracked files are never swept in.

**Reset the workshop:**

```bash
python .workshop/scripts/advance_step.py --reset --auto-commit
```

Your previous `travel_assistant/` is preserved under `.workshop_instance/workshop_backups/reset-<timestamp>/`.

**Re-run preflight:**

```bash
python .workshop/scripts/preflight.py
```

**Shortcuts (optional):** the repo ships a `Makefile` with three aliases:

```bash
make advance     # advance to the next step (auto-commits workshop paths)
make reset       # reset to step 0 (auto-commits workshop paths)
make preflight   # run environment checks
```

When `make` is not available (e.g. on a clean Windows install), just run the equivalent `python .workshop/scripts/...` commands above.

**When the button and the local flow are interchangeable.** Both write the same files. You can switch back and forth between clicking the button and running the script across steps without breaking anything — the script's state-sync check will catch any genuine drift before it advances.

## Troubleshooting

### "Workflow cannot push to main"

A branch protection rule is blocking `GITHUB_TOKEN` from pushing the README update. Fix the rule in **Settings → Branches**, or allow the workflow/bot account to bypass the rule for this workshop repository.

### "▶ Start the workshop" returns 404 (URL contains `%7B%7B`)

This means the **Initialize workshop** Action hasn't run yet in your repo, so the button's URL still contains URL-encoded handlebars (`%7B%7B...%7D%7D`) where your repo owner and name should appear. To recover:

1. Confirm Actions are enabled: **Settings → Actions → General → Allow all actions**.
2. Confirm workflows can write: **Settings → Actions → General → Workflow permissions → Read and write permissions**.
3. Open the **Actions** tab, choose **Initialize workshop**, click **Run workflow** on the default branch.
4. Wait for the run to finish, then `git pull` locally (or refresh the GitHub UI). The button URL will now contain your real owner/repo and work on the first click.

If you advanced past step 0 already and only just hit this, the **Start the workshop** Action also self-heals — running it from the Actions tab will perform the missed initialization in the same commit.

### "Actions are disabled"

Enable Actions in **Settings → Actions → General → Allow all actions**.

### "Third-party actions blocked"

This workshop does not use marketplace actions for advancing steps; it uses the repo's workflow plus plain `git push`. If your organization shows this warning, no third-party action exception is needed for the workshop advance flow.

### "My push didn't advance the step"

Auto-advance only runs for pushes to `main` in your own (non-template) repo, and it skips pushes that only changed workshop bookkeeping such as `.workshop_instance/.workshop-state.json`. Check the **Actions** tab for the **Advance workshop on push to main** run. If it was skipped, make sure you pushed a real change to `main` and that the previous advance already finished. If several quick pushes collapsed into a single advance, that's expected — each *landed* push advances one step.

### "Codespace can't reach my Foundry project"

`DefaultAzureCredential` may pick up the Codespace's GitHub token before your Azure CLI identity. Add `AZURE_TENANT_ID` to your environment, or run this in the Codespace terminal:

```bash
az login --use-device-code
```

### "My edits are gone after advancing"

They were backed up to `.workshop_instance/workshop_backups/step-<previous>/` in the same commit that advanced the workshop. Cherry-pick or copy back anything you want to keep.

## Cleanup

When you finish, or if you want to abandon the workshop, step 99 runs `python .workshop/scripts/cleanup.py --apply` to delete all workshop-created Azure resources. The script only touches resources whose names start with `WORKSHOP_RESOURCE_PREFIX`. If you used `azd` to provision hosted-agent resources, you can alternatively run `azd down` to tear down the resources `azd` created.

---

**Initialization is automatic:** in your own copy, push once and let **Initialize workshop** run.

**If needed:** run **Actions → Initialize workshop** manually.

**Prefer local only?** Run `python .workshop/scripts/advance_step.py --init`.

Then refresh/pull: your README will be rewritten to Step 00 and from there you can advance normally.
