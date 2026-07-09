> **Welcome — start here.** Step 0 has no agent code. It introduces the workshop, walks you through creating your own copy of the repository, and gets your local toolchain ready so Step 1 can jump straight into building the first agent.

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

## Open your repo

Pick one of the two paths — the rest of the workshop works the same either way.

### Option A — GitHub Codespaces (recommended)

In your new repo, click **Code → Codespaces → Create codespace on `main`**. The first build takes ~2 minutes; after that the included devcontainer has everything pre-installed:

- Python 3.12, `az` CLI (with Bicep), `azd` CLI, `uv`, `git`, `gh`, Node.js, GitHub Copilot CLI (`copilot`)
- VS Code extensions: **Python**, **Pylance**, **Python Debugger**, **Foundry Toolkit**, **Bicep**, **Azure MCP Server (Azure Skills)**, **YAML**, **GitHub Pull Requests**
- The **Azure Skills** plugin for the GitHub Copilot CLI is installed (its Azure MCP + Foundry MCP tools require `az login` at use time)
- The post-create step has already created `.venv/` and installed workshop dependencies from `travel_assistant/requirements.txt` (or `.workshop/step_files/00/requirements.txt`) plus `.workshop/scripts/requirements.txt`

> ⚠️ **Wait for "Initialize workshop" to finish first.** If you create the Codespace before that Action has applied step 0, the container falls back to `.workshop/step_files/00/requirements.txt` for workshop deps. After the Action turns green, rebuild the Codespace (Command Palette → **Codespaces: Rebuild Container**) so it picks up `travel_assistant/requirements.txt`.

If you go this route, **skip the "Install the tools you'll need" section** below and jump straight to **"Set up your local environment (one-time)"**.
👉 Direct link: [Set up your local environment (one-time)](#set-up-your-local-environment-one-time)

### Option B — Clone locally

```bash
git clone https://github.com/<your-owner>/<your-repo>.git
cd <your-repo>
```

Then continue with **"Install the tools you'll need"** below to install Python, `az`, `azd`, and (optionally) `uv` on your machine.

## Install the tools you'll need

> 💡 **In a Codespace?** Skip this section — the devcontainer already installed all of these. Jump to **"Set up your local environment (one-time)"**.

**Prerequisites at a glance:**

- **Azure subscription** with access to a Foundry project and a deployed model such as `gpt-4o-mini` or `gpt-4.1-mini`. See [Create a Foundry project](https://learn.microsoft.com/azure/ai-foundry/how-to/create-projects).
- **A role that lets you *use* the project** — **`Foundry User`** (formerly *Azure AI User*) on the Foundry project. This is the least-privilege role for *using* a project — prefer it over broader roles like Owner or Contributor. If you created the project you already have at least this. Some steps assign extra roles as needed (Step 5 adds Azure AI Search roles; Step 6 reuses `Foundry User` for the Skills API and grants it to the deployed agent's identity).
- **Python 3.10 or newer** (the devcontainer ships 3.12).
- **Azure CLI (`az`)** — used by `DefaultAzureCredential` for local auth.
- **Azure Developer CLI (`azd`)** with the [`microsoft.foundry` extension](https://learn.microsoft.com/azure/foundry/agents/how-to/install-cli-foundry-extensions) — used to scaffold, provision, run, and deploy hosted agents.
- **VS Code + [Foundry Toolkit](https://marketplace.visualstudio.com/items?itemName=ms-windows-ai-studio.windows-ai-studio)** *(optional, recommended)* — UI alternative to `azd` for running, debugging, and deploying hosted agents.
- **[GitHub Copilot CLI](https://github.com/github/copilot-cli) (`copilot`)** *(optional, recommended)* — AI-powered CLI assistant. Install with `npm install -g @github/copilot` (the devcontainer installs it automatically via the `copilot-cli` feature).
- **[Bicep](https://learn.microsoft.com/azure/azure-resource-manager/bicep/install)** *(optional, recommended)* — infrastructure-as-code language for the `azd`-generated `infra/`. Install the CLI with `az bicep install` and the [Bicep VS Code extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-bicep) for language support.
- **[Azure Skills](https://github.com/microsoft/azure-skills)** *(optional, recommended)* — Azure skills and MCP server configurations for AI coding assistants. In VS Code install the [Azure MCP extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-azure-mcp-server); for the GitHub Copilot CLI run `/plugin marketplace add microsoft/azure-skills` then `/plugin install azure@azure-skills`. Requires **Node.js 18+** (the MCP servers run via `npx`) and an authenticated `az login` for the Azure tools.
- **[`uv`](https://docs.astral.sh/uv/)** *(optional)* — a faster drop-in for `pip`/`venv`. Anywhere this workshop says `pip` or `python -m venv` you can use `uv pip` or `uv venv` instead.

### Python 3.10+

- **Windows:**
  ```powershell
  winget install --id Python.Python.3.12 -e
  ```
- **macOS:** install from [python.org](https://www.python.org/downloads/) or `brew install python@3.12`.
- **Linux (Ubuntu/Debian):**
  ```bash
  sudo apt update && sudo apt install -y python3 python3-venv python3-pip
  ```

Verify:

```bash
python --version  # or `python3 --version`
```

### Azure CLI (`az`)

Full instructions: [Install the Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli).

- **Windows:**
  ```powershell
  winget install --id Microsoft.AzureCLI -e
  ```
- **macOS:**
  ```bash
  brew update && brew install azure-cli
  ```
- **Linux (Ubuntu/Debian):**
  ```bash
  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
  ```

Verify:

```bash
az --version
```

### Azure Developer CLI (`azd`)

Full instructions: [Install the Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd).

- **Windows:**
  ```powershell
  winget install --id Microsoft.Azd -e
  ```
- **macOS:**
  ```bash
  brew tap azure/azd && brew install azd
  ```
- **Linux:**
  ```bash
  curl -fsSL https://aka.ms/install-azd.sh | bash
  ```

Verify:

```bash
azd version
```

### Optional: `uv` (faster Python package manager)

Full instructions: [Install `uv`](https://docs.astral.sh/uv/getting-started/installation/).

- **Windows:**
  ```powershell
  winget install --id=astral-sh.uv -e
  ```
- **macOS / Linux:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

## Set up your local environment (one-time)

> 💡 **In a Codespace?** The devcontainer already handled the venv and the `pip install` (substeps 3 and 5 below) — but you still need to do **substeps 1, 2, 4, and 6** (`az login`, `azd auth login` + ext install, copy `.env`, run preflight). Authentication and your `.env` can't be baked into the container.

1. **Sign in to Azure**:
   ```bash
   az login
   ```
   If needed, select a subscription:
   ```bash
   az account set -s <subscription>
   ```
2. **Sign in to azd and install the Foundry extension** (one-time):
   ```bash
   azd auth login
   azd ext install microsoft.foundry
   ```
   This adds the `azd ai agent ...` subcommands used from Step 1 onward to scaffold `azure.yaml`/`infra/`, provision Foundry resources, run the agent locally, deploy, and invoke. `microsoft.foundry` is a meta-package that installs all the Foundry `azd ai` extensions — see [Install the Azure Developer CLI Foundry extensions](https://learn.microsoft.com/azure/foundry/agents/how-to/install-cli-foundry-extensions) for details. If you'd rather drive everything from VS Code, the **Foundry Toolkit** extension exposes the same operations as palette commands and a sidebar. If you have a Python virtual environment active (next substep), the Foundry Toolkit picks it up automatically when you press **F5** to debug.
3. **Create and activate a Python virtual environment.**

   Pick one of the two options below. Option A uses the Python stdlib `venv` module and matches the rest of the workshop; Option B uses [`uv`](https://docs.astral.sh/uv/), which is significantly faster but requires installing `uv` first (see above). Both options create the environment at `.venv/`, so the activation commands are the same.

   **Option A — `python -m venv` (default)**
   ```bash
   python -m venv .venv
   ```

   **Option B — `uv venv`**
   ```bash
   uv venv .venv
   ```

   Then activate it:

   - macOS / Linux:
     ```bash
     source .venv/bin/activate
     ```
   - Windows (PowerShell):
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```

   > 💡 **`uv` users:** activation is optional. `uv pip install`, `uv run`, and `uv` itself auto-discover `.venv/` in the current directory. If you skip activation, prefix later Python commands with `uv run` (e.g. `uv run python .workshop/scripts/preflight.py`) so they use the venv's interpreter.
4. **Configure environment**: setup and every later step read their configuration from a repo-root `.env` file, so create it **before** running preflight — copy the template, then fill it in:

   ```bash
   # bash / zsh
   cp .env.example .env
   ```

   ```powershell
   # PowerShell
   Copy-Item .env.example .env
   ```

   Then edit `.env`:
   - `AZURE_AI_PROJECT_ENDPOINT` — from your Foundry project's overview page.
   - `AZURE_AI_MODEL_DEPLOYMENT_NAME` — your deployment name, for example `gpt-4o-mini`.
   - `WORKSHOP_RESOURCE_PREFIX` — this prefixes **every** Azure/Foundry resource the workshop creates (and is how `.workshop/scripts/cleanup.py` finds them later). If you're working solo in your own subscription, leave the default `foundry-workshop`. But if you **share the Foundry project or subscription** with other people running this workshop, change it to a value unique to you (for example `foundry-workshop-<your-alias>`) so your resource names don't **collide** with a teammate's — otherwise provisioning can fail on name conflicts, and cleanup could delete each other's resources.
   - Leave step-specific variables empty for now; the README will tell you when to fill them.
5. **Install dependencies** (use the option that matches your venv choice above):

   **Option A — `pip`**
   ```bash
   pip install -r travel_assistant/requirements.txt
   pip install -r .workshop/scripts/requirements.txt
   ```

   **Option B — `uv pip`**
   ```bash
   uv pip install -r travel_assistant/requirements.txt
   uv pip install -r .workshop/scripts/requirements.txt
   ```
6. **Run preflight**:
   ```bash
   # If you activated .venv
   python .workshop/scripts/preflight.py

   # If you're using uv without activation
   uv run python .workshop/scripts/preflight.py
   ```
   Fix any ❌. ⚠️ items are usually safe to ignore until later steps.

## How the workshop works

This workshop has one important contract: **`README.md` is the current step**. Each time you advance, the repository rewrites `README.md` so the next set of instructions appears in place.

- **Leaving Setup (this step):** click the **▶ Start the workshop** button at the bottom. It opens the workshop's GitHub Action — click **Run workflow**, and it moves you from Setup to Step 1.
- **Every step after that:** there is no button. When you finish a step, **commit the files you created and push them to `main`**. The push triggers the **Advance workshop on push to main** Action, which loads the next step. Each landed push advances by exactly **one** step, so push once — when the step is done.
- After the Action finishes, run **`git pull`** locally. If you are reading in the GitHub UI, refresh the page to see the new `README.md`.
- Advancing lays the next step's canonical files **on top of** your `travel_assistant/` directory. Files from earlier steps that the next step doesn't touch are kept as-is — nothing is deleted. Files the next step ships are refreshed to that step's version, and your current edits are first saved to `.workshop_instance/workshop_backups/step-<N>/` in the same commit so you can recover your own wording.

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

Make sure `python .workshop/scripts/preflight.py` is green (or `uv run python .workshop/scripts/preflight.py` if you're using `uv` without activation), then click the button below to open the workflow — and click **Run workflow** in the dialog that appears:

[![▶ Start the workshop](https://img.shields.io/badge/%E2%96%B6_Start_the_workshop-Step_01-2ea44f?style=for-the-badge)](https://github.com/{{OWNER}}/{{REPO}}/actions/workflows/start-workshop.yml)

Click **Run workflow** to move from Setup to Step 1. Pull after the action completes. From Step 1 onward you advance by committing your work and pushing to `main` — there is no button. With setup already done, Step 1 jumps straight into authoring `agent.yaml`, `agent.manifest.yaml`, and `main.py` for your first hosted TravelBuddy agent.

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

**Pull the latest workshop machinery (without advancing):**

```bash
python .workshop/scripts/sync_template.py --auto-commit   # add --push to push too
```

Occasionally the upstream template ships fixes to the workshop machinery (the authoring material under `.workshop/` and the GitHub configuration under `.github/`). This pulls those into your instance **without moving to the next step** and without touching your `travel_assistant/`, your `.workshop_instance/` state, `README.md`, or `.env`. The commit carries a `[skip-advance]` marker, so pushing it never advances you. A local run also refreshes `.github/workflows/`; the automated CI sync deliberately skips workflow files so it stays tokenless (no Personal Access Token required).

**Reset the current step (re-lay its clean starter files):**

```bash
python .workshop/scripts/advance_step.py --reset-current --auto-commit
```

Re-lays the **current** step's clean starter files and re-renders its `README.md`, staying on the current step — unlike `--reset`, which returns you to step 0. Your previous `travel_assistant/` is backed up under `.workshop_instance/workshop_backups/reset-current-<step>-<timestamp>/` first. Pair it with a sync when you want the current step's delivery refreshed too: **sync first, then reset the current step**. (If you sync just before advancing, you don't need this — advancing already lays down fresh files.)

**Re-run preflight:**

```bash
# If you activated .venv
python .workshop/scripts/preflight.py

# If you're using uv without activation
uv run python .workshop/scripts/preflight.py
```

**Shortcuts (optional):** the repo ships a `Makefile` with these aliases:

```bash
make advance        # advance to the next step (auto-commits workshop paths)
make reset          # reset to step 0 (auto-commits workshop paths)
make reset-current  # re-lay the current step's clean files (auto-commits)
make preflight      # run environment checks
make sync-template  # pull latest .workshop/ + .github/ from the template
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

Auto-advance only runs for pushes to `main` in your own (non-template) repo, and it skips a few kinds of push that aren't step progress: pushes that only changed workshop bookkeeping such as `.workshop_instance/.workshop-state.json`, pushes whose commit carries a `[skip-advance]` marker (what a template sync adds), and pushes that changed **only** workshop machinery or platform files (`.github/`, `.workshop/`, `Makefile`, `.devcontainer/`, `README.md`, …) and never your delivery (`travel_assistant/` or its sibling folders like `travel_toolbox/`). That last case means you can pull the latest machinery from upstream and push it — even manually, without the `[skip-advance]` marker — without being bumped to the next step. Check the **Actions** tab for the **Advance workshop on push to main** run. If it was skipped, make sure your push touched a delivery file and that the previous advance already finished. If several quick pushes collapsed into a single advance, that's expected — each *landed* push advances one step.

### "Codespace can't reach my Foundry project"

`DefaultAzureCredential` may pick up the Codespace's GitHub token before your Azure CLI identity. Add `AZURE_TENANT_ID` to your environment, or run this in the Codespace terminal:

```bash
az login --use-device-code
```

### "My edits are gone after advancing"

They were backed up to `.workshop_instance/workshop_backups/step-<previous>/` in the same commit that advanced the workshop. Cherry-pick or copy back anything you want to keep.

## Cleanup

When you finish, or if you want to abandon the workshop, step 99 runs `python .workshop/scripts/cleanup.py --apply` to delete all workshop-created Azure resources. The script only touches resources whose names start with `WORKSHOP_RESOURCE_PREFIX`. If you used `azd` to provision hosted-agent resources, you can alternatively run `azd down` to tear down the resources `azd` created.

## Solution

This step has no code to write — it's intro and setup of your repo from the template.