> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

> 🧪 **Experimental** — this step has not been fully tested yet. Treat it as a preview and expect rough edges.

# Solution 08 — TravelBuddy as a durable workflow

Builds on Step 7: the same Flights / Hotels / Activities specialists are reused as **workflow nodes** instead of runtime handoff targets. A `gather_preferences` step fans the request out to all three specialists, a `consolidate` step aggregates their answers and **checkpoints** the draft, and a `finalize_itinerary` step produces the plan. The graph is exposed as one hosted agent via `workflow.as_agent()`, so hosting and deployment are unchanged (`resources: []`, no `azd provision`).

## Layout

- `travel_assistant/` — the agent code.
  - `coordinator.py` — Step 7 handoff **plus** the extracted specialist factories (`create_flights_agent`, `create_hotels_agent`, `create_activities_agent`, `make_client`) that both the coordinator and the workflow now share.
  - `workflow.py` — the executors (`GatherPreferences`, `Consolidate`, `ApprovalGate`), the `WorkflowBuilder` graph, checkpointing, and `build_workflow_agent()`.
  - `coordinator.py`'s `_build_skills_provider` downloads the required Foundry skill at runtime into a writable temp dir (`<tempdir>/foundry_downloaded_skills/`) and serves it plus the local skill via one `SkillsProvider`; `workflow.py` attaches it to the `finalize_itinerary` step.
  - `main.py` — hosts `build_workflow_agent()`.
  - `agents/`, `tools.py`, `skills/`, `data/` — carried from Steps 4–7.
- `travel_indexer/` — the out-of-band Search indexer (`provision_index.py`, `data/destinations.json`), a sibling of `travel_assistant/` (from Step 5).
- `foundry_skills/` — the out-of-band Foundry-skill authoring + upload (`provision_skills.py`, `skills/response-guardrails/SKILL.md`), a sibling of `travel_assistant/` (from Step 6). Never deployed.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`).
- `.env.example` — shared configuration, including `FOUNDRY_SKILL_NAMES`.

## Graph

```
gather_preferences ─┬─► flights ────┐
                    ├─► hotels ─────┼─► consolidate (💾 checkpoint) ─► finalize_itinerary
                    └─► activities ─┘
```

`build_workflow(require_approval=True)` inserts an `approval_gate` between `consolidate` and `finalize_itinerary`. When hosted as an agent, that gate's `request_info` call is surfaced to the caller as a function call; run it locally with a streaming response loop for a fully interactive approval.

## Run it

1. Copy `.env.example` to `.env` and fill in values (including `FOUNDRY_SKILL_NAMES`).
2. Sign in with Entra ID: `az login`. Uploading and downloading Foundry skills needs **Foundry User** (formerly Azure AI User) on the Foundry project, and the account must allow **public network access** (the Skills API does not support private networking).
3. Install dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Provision the destinations index once (from Step 5): `python travel_indexer/provision_index.py`.
5. Upload the Foundry skill once (re-run after editing its `SKILL.md`): `python foundry_skills/provision_skills.py`.
6. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try: `Plan a 5-day Tokyo trip for two with a budget of €3000.` — expect all three specialists to contribute before the itinerary is finalized. Checkpoints are written under `.workshop-state/workflow-checkpoints/`.

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from the upstream [`05-workflows`](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/05-workflows) sample.
