> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

# Solution 05 — TravelBuddy with destination RAG

Builds on Step 4: keeps the function tools (`get_weather`, `get_local_time`, `convert_currency`) and the **Foundry Toolbox** (web search, Code Interpreter, OctoTrip flights), and adds **RAG grounding** over a curated destinations index in Azure AI Search via `AzureAISearchContextProvider`.

## Layout

This solution mirrors the repo layout after advancing to Step 5 — three sibling folders plus a shared `.env`:

- `travel_assistant/` — the agent (`main.py`, `tools.py`, `agent.yaml`, `agent.manifest.yaml`, `requirements.txt`, `Dockerfile`, `data/itinerary.csv`). This is what `azd ai agent init` snapshots.
- `travel_indexer/` — the out-of-band Search indexer (`provision_index.py`, `data/destinations.json`), a sibling of `travel_assistant/` so the provisioning script and corpus are never swept into the deployed agent snapshot.
- `travel_toolbox/` — the toolbox definition (`toolbox.yaml`), a sibling of `travel_assistant/` so it is never swept into the agent snapshot.
- `.env.example` — shared configuration, a sibling of all three folders.

## Run it

1. Copy `.env.example` to `.env` and fill in values (Foundry, `TOOLBOX_ENDPOINT`, and the two `AZURE_AI_SEARCH_*` values).
2. Sign in with Entra ID: `az login`.
3. Install the agent dependencies: `pip install -r travel_assistant/requirements.txt`.
4. Provision the destinations index once: `python travel_indexer/provision_index.py`.
5. Start the hosted Responses server: `cd travel_assistant && python main.py`.

Try: `What does our destinations index say about Reykjavik in winter, and find flights from Seattle (SEA) to Lisbon (LIS) on 2026-05-03.`

To prove retrieval actually ran, ask `What is TravelBuddy's internal concierge desk code for Lisbon?` — it should return `LIS-CANARY-4718`, a canary token that lives only in the index and can't be recalled from training data.

Your Entra ID needs **`Search Service Contributor`** (create the index) and **`Search Index Data Contributor`** (upload/query) on the Search resource to run `provision_index.py`.

When you **deploy** to Foundry, the agent no longer queries as your user — its `AzureAISearchContextProvider` uses `DefaultAzureCredential`, which inside the container resolves the agent's **instance identity** (a per-agent Microsoft Entra service principal). Grant *that* identity the least-privilege **`Search Index Data Reader`** role on the Search service (not a broader `Data Contributor`), or the deployed agent returns `Forbidden`. Resolve the identity from the agent (post-deploy), then assign:

```bash
AGENT_NAME="${WORKSHOP_RESOURCE_PREFIX}-travel-buddy"   # AZURE_AI_PROJECT_ENDPOINT is already in your .env
AGENT_IDENTITY="$(az rest --method GET --url "${AZURE_AI_PROJECT_ENDPOINT}/agents/${AGENT_NAME}?api-version=v1" \
  --resource "https://ai.azure.com" --query "instance_identity.principal_id" -o tsv)"
az role assignment create --assignee-object-id "$AGENT_IDENTITY" \
  --assignee-principal-type ServicePrincipal --role "Search Index Data Reader" \
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<search-name>"
```

This grant is **one-time per agent** and survives `azd deploy` redeploys (the instance identity belongs to the agent, not the version). Re-grant only if you delete/recreate or **publish** the agent (publishing creates a distinct identity — prior roles don't carry over). The project managed identity handles *platform* ops (model proxy, ACR pull, Log Analytics), not your code's Search calls. A managed **Foundry IQ** knowledge base is the exception — Foundry retrieves as the project MI. See [manage-hosted-agent](https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-agent#retrieve-the-agent-identity-for-role-assignments), [agent-identity](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity), and [foundry-iq-connect#prerequisites](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect#prerequisites).

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from [11-azure-search-rag](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/11-azure-search-rag) in `microsoft-foundry/foundry-samples`.
