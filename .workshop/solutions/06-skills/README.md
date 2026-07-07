> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

# Solution 06 — TravelBuddy with local **and** Foundry Skills

Builds on Step 5: keeps the function tools, the **Foundry Toolbox** (web search, Code Interpreter, OctoTrip flights), and **RAG** over the destinations index, and adds **two** Skills:

- a **local** Skill (`skills/travel-guide/`) that ships with the agent — a `SKILL.md` prompt/schema plus `scripts/create_travel_guide.py`, which renders a colorful, downloadable PDF travel guide (with a day-by-day itinerary) grounded in the destinations index; and
- a required **Foundry** Skill (`response-guardrails`) uploaded to the Foundry project out-of-band and downloaded by the agent at startup — reusable, domain-agnostic Responsible-AI guardrails that can be shared across agents in the project.

Both skills are served by a **single** `SkillsProvider.from_paths([local, downloaded], script_runner=...)`: the runner runs the local skill's `create_travel_guide.py`, while the Foundry skill is instruction-only, so the runner never fires for it.

## Layout

Four sibling folders plus a shared `.env` (same shape as after advancing to Step 6):

- `travel_assistant/` — the agent. Adds `skills/travel-guide/` (local) and a small startup download in `main.py` that fetches the Foundry skill into a writable temp dir (`<tempdir>/foundry_downloaded_skills/`) before serving both via one `SkillsProvider`. This is what `azd ai agent init` snapshots.
- `travel_indexer/` — the out-of-band Search indexer (`provision_index.py`, `data/destinations.json`), a sibling of `travel_assistant/` (from Step 5).
- `foundry_skills/` — the out-of-band Foundry-skill authoring + upload (`provision_skills.py`, `skills/response-guardrails/SKILL.md`), a sibling of `travel_assistant/`. Never deployed — like `travel_indexer/`, `azd ai agent init` snapshots only `travel_assistant/`.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`), a sibling of `travel_assistant/`.
- `.env.example` — shared configuration, including `FOUNDRY_SKILL_NAMES`.

**Source-of-truth discipline:** edit `foundry_skills/skills/response-guardrails/SKILL.md`, then re-run `provision_skills.py`. The agent re-downloads the skill at runtime into a writable temp dir (`<tempdir>/foundry_downloaded_skills/`) — never edit that copy (it is a throwaway cache, recreated each startup). Two copies is expected, and mirrors the indexer (source JSON vs. the live index).

## Run it

1. Copy `.env.example` to `.env` and fill in values (Foundry, `TOOLBOX_ENDPOINT`, the two `AZURE_AI_SEARCH_*` values, and `FOUNDRY_SKILL_NAMES`).
2. Sign in with Entra ID: `az login`. Uploading and downloading Foundry skills needs **Foundry User** (formerly Azure AI User) on the Foundry project, and the account must allow **public network access** (the Skills API does not support private networking).
3. Install dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Provision the destinations index once (from Step 5): `python travel_indexer/provision_index.py`.
5. Upload the Foundry skill once (re-run after editing its `SKILL.md`): `python foundry_skills/provision_skills.py`.
6. Smoke-test the local skill script directly: `python travel_assistant/skills/travel-guide/scripts/create_travel_guide.py --city Lisbon --days 4`.
7. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try:

- `Make me a downloadable 4-day Lisbon travel guide using our destinations index. I like food, viewpoints, and history.` — loads the **local** travel-guide skill and writes a PDF.
- `Is it safe to travel to that region right now, and what vaccinations do I need?` — triggers the **Foundry** response-guardrails skill; the response should end with `GUARDRAILS-APPLIED`.

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from [07-skills](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/07-skills) (local Skill) and [12-foundry-skills](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/12-foundry-skills) (Foundry Skill) in `microsoft-foundry/foundry-samples`.
