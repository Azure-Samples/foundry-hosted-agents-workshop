# Copilot instructions

Guidance for GitHub Copilot (and any AI coding assistant) working in this
repository or in a copy made from it.

## Who this is for

This file ships inside the workshop template, so it guides **two** audiences:

- **Workshop builders** (students): you clicked *Use this template* and are
  building your own **TravelBuddy** hosted agent by following the steps under
  `.workshop/docs/steps/`. You write and edit agent code (`main.py`, tools,
  skills, manifests) in `travel_assistant/` at the repo root — that is **the
  delivery**, and it is where your focus belongs. You do not author the workshop
  itself.
- **Workshop maintainers**: you are editing the workshop content (the step docs,
  `.workshop/step_files/`, and `.workshop/solutions/`). Your rules live in
  [`.github/instructions/workshop.instructions.md`](instructions/workshop.instructions.md),
  which applies whenever you work under `.workshop/**`.

The **guiding principles** below apply to everyone. The **conventions** are
split so each audience knows which rules are theirs.

## Repository layout

The repo separates **delivery** from **workshop machinery** so participants stay
focused on the agent they build:

- **Repo root — delivery + platform.** `travel_assistant/` (the delivery you
  build), `README.md` (your current step, rendered), `Makefile`, `.env.example`,
  `.devcontainer/`, `.vscode/`, `CONTRIBUTING.md`, and `.github/` (workflows +
  these instructions).
- **`.workshop/` — workshop authoring material** (hidden, still tracked/shipped):
  `docs/` (steps, partials, assets), `step_files/`, `solutions/`, and `scripts/`
  (the advance engine + tests). Run the scripts from the repo root, e.g.
  `python .workshop/scripts/advance_step.py` (or the `make` shortcuts).
- **`.workshop_instance/` — per-instance runtime state** (hidden, tracked; the
  advance engine commits it): `.workshop-state.json`, `workshop_backups/`, and
  the init marker `.workshop-initialized` (created only in a generated instance;
  never committed in the template).

## Guiding principles

### 1. KISS - Keep It Simple

- Prefer the simplest solution that fully solves the problem. Do not add
  abstraction, configuration, or flags "in case we need them later."
- This is teaching code: keep it clean, expanded, and readable. Never minify,
  golf, or collapse steps to save a line.
- Mirror the patterns already in the repo (for example the way
  `travel_indexer/` and `travel_toolbox/` sit beside `travel_assistant/`)
  instead of inventing a new shape.

### 2. Boy Scout rule - Leave it cleaner than you found it

- When you touch a file, tidy its immediate surroundings: a stale comment, a
  broken link, an inconsistent name, a dead import.
- Do not expand scope: clean what you are already working in, not the whole
  repo. Unrelated refactors belong in their own change.
- Remove things that no longer serve a purpose rather than leaving them to rot.

### 3. Zero Trust - Security by design and by default

- **Secure by design and by default.** The safe path is the default path.
  Following the steps verbatim should produce a secure setup with no extra
  hardening work.
- **Least privilege.** Grant the narrowest role, at the narrowest scope, for
  the shortest time. Prefer specific data-plane roles (e.g. `Foundry User`,
  `Search Index Data Reader`) over `Contributor`/`Owner`. Never propose an
  Owner/Contributor role where a specific role works, and scope assignments to
  the exact resource - not the subscription or resource group.
- **No passwords, no keys, no secrets.** Authenticate with Microsoft Entra ID
  via `DefaultAzureCredential` (local `az login`; deployed = the agent's
  managed/instance identity). Do not introduce API keys, connection strings
  with embedded secrets, account keys, or SAS tokens, and do not disable RBAC
  or fall back to key auth to "make it work."
- **Trust nothing implicitly.** Validate and constrain external input and
  downloaded content (for example the zip-slip guard and placeholder rejection
  when unpacking a downloaded Foundry skill). Never arm a trusted local script
  runner over content that came from outside the repo.

## Conventions for everyone

- **Keyless auth only.** Every access to an Azure resource goes through
  `DefaultAzureCredential` / Entra ID / managed identity.
- **No secrets in the repo.** `.env` holds your local values and is never
  committed; `.env.example` carries only key names and safe placeholders.
- **Commands: `uv` first.** Offer the `uv` variant first (`uv run ...`,
  `uv pip ...`, `uv venv`), then the plain `pip` / `python -m venv` equivalent.
- **Never edit or commit runtime-download dirs** (e.g.
  `foundry_downloaded_skills/`) - they are git/azd/docker-ignored and
  regenerated at runtime.
- **Manifests use `resources: []`** unless a step genuinely adds a non-model
  resource; the model is selected at runtime via
  `AZURE_AI_MODEL_DEPLOYMENT_NAME`.

## Maintaining the workshop itself

Editing anything under `.workshop/**` (step docs, `step_files/`, `solutions/`,
`scripts/`)? The overlay model, source-of-truth order, and the lint command are
defined in
[`.github/instructions/workshop.instructions.md`](instructions/workshop.instructions.md),
which the tooling applies automatically for that path. Follow it, and keep the
step docs, `.workshop/solutions/`, and `.workshop/step_files/` in sync.

## Review checklist

Review every change against this list and call out anything you cannot satisfy.

- **No secrets.** No passwords, API keys, connection strings, account keys, SAS
  tokens, or credentials in code, docs, config, commit messages, or
  `.env.example`.
- **Auth is keyless.** Every Azure access uses `DefaultAzureCredential` / Entra
  ID / managed identity - never a key or shared secret.
- **Least privilege respected.** Role assignments use the narrowest role at the
  narrowest scope; no Owner/Contributor where a specific role suffices; each new
  permission is justified (with a doc link where relevant).
- **Untrusted input is validated.** Anything downloaded, unzipped, or supplied
  externally is bounded and checked; local execution privileges are not
  extended to remote content.
- **Simple and consistent (KISS).** No needless abstraction; the change matches
  existing repo patterns and teaching style.
- **Left cleaner (Boy Scout).** Touched files have no new dead code, stale
  comments, or broken links.
- **Maintainers:** docs and their matching `.workshop/solutions/` /
  `.workshop/step_files/` stay in sync, and `python .workshop/scripts/lint_steps.py`
  reports 0 failures.
