> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

# Solution 04 — TravelBuddy with Foundry Toolbox

Keeps the Step 2 function tools (`get_weather`, `get_local_time`, `convert_currency`) and migrates the Step 3 OctoTrip flights MCP into a **Foundry Toolbox** that also bundles `web_search` and `code_interpreter`. The agent consumes the toolbox with `FoundryToolbox(credential)`, which reads the `TOOLBOX_ENDPOINT` environment variable.

## Layout

This solution mirrors the repo layout after advancing to Step 4 — two sibling folders plus a shared `.env`:

- `travel_assistant/` — the agent (`main.py`, `tools.py`, `agent.yaml`, `agent.manifest.yaml`, `requirements.txt`, `Dockerfile`, `data/itinerary.csv`). This is what `azd ai agent init` snapshots.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`). It lives in its own folder, a sibling of `travel_assistant/`, so it is never swept into the agent snapshot by `azd ai agent init`.
- `.env.example` — shared configuration, a sibling of both folders (mirrors the repo-root `.env`).

## Run it

1. Copy `.env.example` to `.env` and fill in values (`AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, `WORKSHOP_RESOURCE_PREFIX`).
2. Sign in with Entra ID: `az login`.
3. Install dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Create the toolbox once from `travel_toolbox/toolbox.yaml` (or the portal), then set the endpoint it prints: `azd env set TOOLBOX_ENDPOINT "<endpoint>"` (also add it to `.env`).
5. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try: `Find flights from Seattle (SEA) to Lisbon (LIS) on 2026-05-03, then read the attached itinerary.csv and total the trip by category in EUR with a chart.`

`travel_assistant/data/itinerary.csv` is the sample file Code Interpreter analyzes.

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from [04-foundry-toolbox](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/04-foundry-toolbox) in `microsoft-foundry/foundry-samples`.
