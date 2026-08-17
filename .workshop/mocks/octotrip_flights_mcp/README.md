# OctoTrip Flights mock MCP server

A stand-in for [`https://mcp.octotrip.app/flights/mcp`](https://github.com/octotrip/flights),
the public flight-search MCP server used in [Step 3](../../docs/steps/03-mcp.md).
Reach for it when the real server is down, rate limiting you, or when you want
answers that don't change between runs.

**Everything it returns is fake.** Airlines, flight numbers, prices, and booking
links are generated from your request — there is no network call and no live
pricing. The airlines are invented (OctoAir, Mockingbird Airways, …), booking
links point at the reserved `.invalid` domain, and every payload carries
`"mock": true` plus a `mock_notice`, so nobody mistakes a fixture for a fare.

## What "generated from the request" means

The mock is not a canned response. It reads your arguments and builds a plausible
answer from them:

- **Real airport coordinates** (60+ airports) give real great-circle distances,
  so flight durations and local departure/arrival times — including next-day
  arrivals across time zones — come out right.
- **Connections** are picked from a global hub list, preferring hubs near the
  midpoint of the route. Short routes only get direct and one-stop offers; no
  non-stop is offered beyond 13 500 km.
- **Prices** scale with distance, stops, cabin class, one-way vs round trip,
  passenger mix, and currency.
- **The same request always returns the same offers.** The random seed is a
  SHA-256 hash of the request, so a demo is reproducible — but change the date,
  the cabin, or the passenger count and the results change with it.

It mirrors the real server's tool contract: one `search` tool, the same
parameters, the same response shape, and the same structured errors
(`airport_not_found`, `disambiguation_needed`, `invalid_date`, `no_results`).

## Run it locally

No dependencies, no install:

```bash
make mock-mcp
# or, without make:
python .workshop/mocks/octotrip_flights_mcp/serve_local.py
```

The MCP endpoint is `http://127.0.0.1:8931/mcp` (health probe at `/health`).
Use `--host` / `--port` to change the binding.

Quick check:

```bash
curl -s http://127.0.0.1:8931/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search",
       "arguments":{"origin":"BRU","destination":"LIS","departure_date":"2026-09-07"}}}'
```

> **Foundry can't call localhost.** `client.get_mcp_tool(...)` registers a
> *hosted* MCP tool: the Foundry service calls the URL from its own network, not
> from your container. So `http://127.0.0.1:8931/mcp` works for `curl` and for a
> local MCP client, but never for your agent. Give it a public URL — a dev tunnel
> (below) while you iterate, or a deployment (further below) for something stable.

## Expose your local run with a dev tunnel

This is the normal inner-loop pattern: keep the server on your machine, where you
can edit and restart it in seconds, and let Foundry reach it through a temporary
public HTTPS URL.

Install the [dev tunnel CLI](https://learn.microsoft.com/azure/developer/dev-tunnels/get-started),
then, with the mock already running on port 8931:

```bash
devtunnel user login
devtunnel create octotrip-mock --allow-anonymous
devtunnel port create octotrip-mock --port-number 8931
devtunnel host octotrip-mock
```

`--allow-anonymous` matters: Foundry calls the tunnel without a dev tunnel token,
so a protected tunnel answers 401 and your agent just sees a broken tool.

`devtunnel host` prints a forwarding URL. Append the endpoint path and point
Step 3 at it:

```env
# .env
MCP_SERVER_LABEL=octotrip_flights_mock
MCP_SERVER_URL=https://<tunnel-id>-8931.<region>.devtunnels.ms/mcp
```

Mirror both values in `agent.manifest.yaml` under `template.environment_variables`,
exactly as Step 3 describes for the real server, and redeploy the agent so it
picks up the new URL.

Two things to keep in mind:

- **The URL changes** every time you create a new tunnel, and the tunnel dies
  when you stop hosting it. Re-run `devtunnel host octotrip-mock` to get the same
  URL back; create a fresh tunnel only if you deleted it.
- **Anonymous means anonymous.** Anyone with the URL can call it while it's up.
  That's acceptable for a mock that invents flights and stores nothing — never do
  it for a service that touches real data or credentials.

In a Codespace or a VS Code dev container you can skip the CLI: forward port 8931
in the **Ports** panel and set its visibility to **Public**, which gives you an
equivalent URL.

## Deploy it to Azure Functions

The same code also runs as an Azure Functions app, using the platform's
[MCP tool trigger](https://learn.microsoft.com/azure/azure-functions/functions-bindings-mcp-tool-trigger?pivots=programming-language-python).
`function_app.py` declares the tool with `@app.mcp_tool()` — which takes the
tool name from the function name, the description from its docstring, and each
parameter's type and requiredness from the signature — plus one
`@app.mcp_tool_property` per parameter for the wording, read from
`octotrip_mock/tool.py` so both hosts stay in step. That needs
`azure-functions` 1.25.0 or later.

Run it locally with [Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
4.0.7030 or later (`func start` from this folder) to get
`http://localhost:7071/runtime/webhooks/mcp` — which you can put through a dev
tunnel exactly as above, using port 7071 and that path.

To deploy, from this folder:

```bash
RESOURCE_GROUP=rg-octotrip-mock
LOCATION=westeurope
STORAGE=stoctotripmock$RANDOM
APP=func-octotrip-mock-$RANDOM

az group create --name $RESOURCE_GROUP --location $LOCATION

az storage account create \
  --name $STORAGE \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS \
  --allow-blob-public-access false

az functionapp create \
  --name $APP \
  --resource-group $RESOURCE_GROUP \
  --storage-account $STORAGE \
  --flexconsumption-location $LOCATION \
  --runtime python \
  --runtime-version 3.11 \
  --deployment-storage-auth-type SystemAssignedIdentity
```

Then make the app talk to storage with its managed identity instead of a
connection string — no keys anywhere:

```bash
PRINCIPAL=$(az functionapp identity assign \
  --name $APP --resource-group $RESOURCE_GROUP --query principalId -o tsv)

STORAGE_ID=$(az storage account show \
  --name $STORAGE --resource-group $RESOURCE_GROUP --query id -o tsv)

az role assignment create \
  --assignee $PRINCIPAL \
  --role "Storage Blob Data Owner" \
  --scope $STORAGE_ID

az functionapp config appsettings set \
  --name $APP --resource-group $RESOURCE_GROUP \
  --settings AzureWebJobsStorage__accountName=$STORAGE

az functionapp config appsettings delete \
  --name $APP --resource-group $RESOURCE_GROUP \
  --setting-names AzureWebJobsStorage

func azure functionapp publish $APP
```

`Storage Blob Data Owner` is scoped to this one storage account, and it is the
narrowest role the Functions host accepts for deployment storage on Flex
Consumption. Delete the resource group when the workshop is over.

### No key needed — but verify it

By default an MCP tool trigger is protected: the endpoint demands the system key
named `mcp_extension`, as `?code=<key>` or an `x-functions-key` header. This
mock's `host.json` opts out with
`extensions.mcp.system.webhookAuthorizationLevel: "Anonymous"`, so the deployed
URL takes no key at all — same as the public server it replaces. (The `system`
nesting is required; a top-level `webhookAuthorizationLevel` under `mcp` is
silently ignored.)

That's safe here because the app stores nothing, reads nothing, and only returns
invented flights. Don't carry the setting into an app that does anything real.

Check that it took effect before wiring up your agent — an unauthenticated
`initialize` should come back with a result, not a 401:

```bash
curl -si -X POST https://$APP.azurewebsites.net/runtime/webhooks/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",
       "capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' | head -n 1
```

If you get a **401**, your Functions host predates the fix for
[azure-functions-mcp-extension#138](https://github.com/Azure/azure-functions-mcp-extension/issues/138),
where `Anonymous` was ignored on host runtimes older than 4.1045.0. Two ways out:

1. Give the app a moment and redeploy — Flex Consumption picks up newer hosts
   automatically, and you can confirm the version under **Diagnose and solve
   problems → Functions Host** in the portal.
2. Or just use the key. Fetch it, keep it in `.env` (never in the manifest, never
   committed), and pass it as a header:

   ```bash
   az functionapp keys list \
     --resource-group $RESOURCE_GROUP --name $APP \
     --query systemKeys.mcp_extension --output tsv
   ```

   ```python
   mcp_tool = client.get_mcp_tool(
       server_label=os.environ["MCP_SERVER_LABEL"],
       server_url=os.environ["MCP_SERVER_URL"],
       headers={"x-functions-key": os.environ["MCP_SERVER_KEY"]},
   )
   ```

   A hosted agent reads that from its own environment, so add `MCP_SERVER_KEY` to
   `template.environment_variables` in `agent.manifest.yaml` — with the value
   supplied at deploy time, not written into the file.

Local runs never ask for a key, whichever level you set.

Point Step 3 at the deployed app:

```env
# .env
MCP_SERVER_LABEL=octotrip_flights_mock
MCP_SERVER_URL=https://<app>.azurewebsites.net/runtime/webhooks/mcp
```

Mirror both values in `agent.manifest.yaml` under `template.environment_variables`,
exactly as Step 3 describes for the real server.

## Layout

| Path | What it is |
| --- | --- |
| `octotrip_mock/airports.py` | Airport table, coordinates, hubs, and the resolver |
| `octotrip_mock/flights.py` | The generator: validation, seeded randomness, itineraries, pricing |
| `octotrip_mock/tool.py` | The `search` tool contract, shared by both hosts |
| `octotrip_mock/server.py` | Dependency-free MCP streamable-HTTP transport |
| `serve_local.py` | `http.server` host for local runs |
| `function_app.py` | Azure Functions host using `@app.mcp_tool` |

Tests live in [`.workshop/scripts/tests/test_mock_octotrip.py`](../../scripts/tests/test_mock_octotrip.py):

```bash
python -m pytest .workshop/scripts/tests/test_mock_octotrip.py
```
