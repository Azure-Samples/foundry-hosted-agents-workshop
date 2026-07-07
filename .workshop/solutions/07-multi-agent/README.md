> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

# Solution 07 — TravelBuddy as a runtime multi-agent handoff

Builds on Step 6: keeps every carried capability (function tools, Foundry Toolbox, RAG, the local travel-guide Skill, and the required Foundry response-guardrails Skill) and splits TravelBuddy into a **Coordinator** plus **Flights / Hotels / Activities** specialists wired with `HandoffBuilder`. The Coordinator routes each turn to the specialist that owns it; specialists hand back for synthesis. The whole graph is exposed as one agent via `workflow.as_agent()`, so hosting is unchanged.

## Layout

- `travel_assistant/` — the agent code. `coordinator.py` builds the handoff graph and each specialist gets a sliced capability set; the Coordinator downloads the Foundry skill at runtime into a writable temp dir (`<tempdir>/foundry_downloaded_skills/`) and serves it plus the local skill via one `SkillsProvider`. `agents/{flights,hotels,activities}/` hold per-specialist `agent.yaml` + `agent.manifest.yaml` slices that document each role's tool/RAG/skill boundary. Snapshotted by `azd ai agent init`.
- `travel_indexer/` — the out-of-band Search indexer (`provision_index.py`, `data/destinations.json`), a sibling of `travel_assistant/` (from Step 5).
- `foundry_skills/` — the out-of-band Foundry-skill authoring + upload (`provision_skills.py`, `skills/response-guardrails/SKILL.md`), a sibling of `travel_assistant/` (from Step 6). Never deployed — `azd ai agent init` snapshots only `travel_assistant/`.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`).
- `.env.example` — shared configuration, including `FOUNDRY_SKILL_NAMES`.

## Capability slices

| Specialist | Tools | RAG | Skill |
| --- | --- | --- | --- |
| Coordinator | — | — | travel-guide, response-guardrails |
| Flights | `get_weather`, `get_local_time`, `convert_currency`, toolbox (flight fares) | — | — |
| Hotels | `convert_currency`, toolbox (web) | destinations index | — |
| Activities | toolbox (web/reference) | destinations index | — |

## Run it

1. Copy `.env.example` to `.env` and fill in values (including `FOUNDRY_SKILL_NAMES`).
2. Sign in with Entra ID: `az login`. Uploading and downloading Foundry skills needs **Foundry User** (formerly Azure AI User) on the Foundry project, and the account must allow **public network access** (the Skills API does not support private networking).
3. Install dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Provision the destinations index once (from Step 5): `python travel_indexer/provision_index.py`.
5. Upload the Foundry skill once (re-run after editing its `SKILL.md`): `python foundry_skills/provision_skills.py`.
6. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try: `Help me plan a 5-day Tokyo trip: flights from Lisbon, a hotel near Shibuya under €200/night, and a day-trip suggestion.` — expect handoffs across all three specialists.

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

No single upstream sample; the handoff pattern follows the Agent Framework multi-agent [handoff orchestration](https://learn.microsoft.com/agent-framework/user-guide/agent-orchestration/handoff) docs.
