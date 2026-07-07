> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

> 🧪 **Experimental** — this step has not been fully tested yet. Treat it as a preview and expect rough edges.

# Solution 09 — TravelBuddy with durable per-user memory

Builds on Step 8: the same durable workflow (gather → specialists → consolidate → finalize) is unchanged, but each specialist now carries a `FoundryMemoryProvider`. The provider extracts stable traveler preferences (home airport, cabin class, budget band, dietary needs, favourite destinations) into a **Foundry Memory Store** and recalls them on later conversations. Because memory attaches as a `context_provider`, hosting and deployment are unchanged (`resources: []`, no `azd provision`).

## Layout

- `travel_assistant/` — the agent code.
  - `coordinator.py` — Step 8 factories **plus** `_build_memory_provider()`; `make_client` now passes `allow_preview=True` so `client.project_client` can reach the preview memory API. Each specialist appends the memory provider to its `context_providers`.
  - `coordinator.py`'s `create_activities_agent` downloads the required Foundry skill at runtime into a writable temp dir (`<tempdir>/foundry_downloaded_skills/`) and serves it plus the local skill via one `SkillsProvider`.
  - `workflow.py`, `main.py` — carried from Step 8, unchanged.
  - `provision_memory_store.py` — one-time, out-of-band creation of the memory store.
  - `agents/`, `tools.py`, `skills/`, `data/` — carried from Steps 4–8.
- `travel_indexer/` — the out-of-band Search indexer (`provision_index.py`, `data/destinations.json`), a sibling of `travel_assistant/` (from Step 5).
- `foundry_skills/` — the out-of-band Foundry-skill authoring + upload (`provision_skills.py`, `skills/response-guardrails/SKILL.md`), a sibling of `travel_assistant/` (from Step 6). Never deployed.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`).
- `.env.example` — shared configuration (adds `FOUNDRY_SKILL_NAMES`, `AZURE_AI_EMBEDDING_MODEL_DEPLOYMENT_NAME`, and `MEMORY_STORE_NAME`).

## How memory is scoped

`_build_memory_provider()` sets `scope="{{$userId}}"` — a hosting placeholder the runtime replaces with the caller's user id, so every traveler gets isolated memories. If `MEMORY_STORE_NAME` is unset, the provider is skipped and TravelBuddy still runs (just without memory).

## Run it

1. Copy `.env.example` to `.env` and fill in values, including an **embedding model deployment**, a `MEMORY_STORE_NAME`, and `FOUNDRY_SKILL_NAMES`.
2. Sign in with Entra ID: `az login`. Uploading and downloading Foundry skills needs **Foundry User** (formerly Azure AI User) on the Foundry project, and the account must allow **public network access** (the Skills API does not support private networking).
3. Install dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Provision the destinations index once (from Step 5): `python travel_indexer/provision_index.py`.
5. Upload the Foundry skill once (re-run after editing its `SKILL.md`): `python foundry_skills/provision_skills.py`.
6. Provision the memory store once: `python travel_assistant/provision_memory_store.py`.
7. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try, in one conversation: `I always fly out of SEA and prefer window seats on a mid-range budget.` Then, in a **new** conversation: `Plan a 4-day trip to Rome for me.` — expect TravelBuddy to recall your home airport, seat, and budget preferences.

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from the upstream [`13-foundry-memory`](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/13-foundry-memory) sample.
