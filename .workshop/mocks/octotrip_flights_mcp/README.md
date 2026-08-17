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
  arrivals across time zones — come out right. Each airport carries its real
  standard-time UTC offset, so the mock has no DST and rounds the half-hour
  zones (India, and friends) to the nearest hour.
- **Connections** are picked from a global hub list, preferring hubs near the
  midpoint of the route, and the whole path is measured so a connection can't
  zig-zag. Short hops are non-stop or nothing; no non-stop is offered beyond
  13 500 km; and a same-day return only exists if the outbound lands in time to
  fly home again.
- **Prices** scale with distance, stops, cabin class, one-way vs round trip,
  passenger mix, and currency.
- **The same request always returns the same offers.** The random seed is a
  SHA-256 hash of the request, so a demo is reproducible on a given Python
  version — but change the date, the cabin, or the passenger count and the
  results change with it.

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
The real server's path, `/flights/mcp`, is accepted too, so swapping just the
host of an existing `MCP_SERVER_URL` works.
Use `--host` / `--port` to change the binding.

Quick check:

```bash
curl -s http://127.0.0.1:8931/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search",
       "arguments":{"origin":"BRU","destination":"LIS","departure_date":"tomorrow"}}}'
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
devtunnel host --port-number 8931 --allow-anonymous
```

That hosts a temporary tunnel and prints a public HTTPS URL. Two flags matter:

- `--allow-anonymous` — Foundry calls the tunnel without a dev tunnel token, so
  a protected tunnel answers 401 and your agent just sees a broken tool.
- No tunnel ID — `devtunnel create <id>` takes a **globally unique** ID, so a
  name like `octotrip-mock` fails for everyone but the first person to claim it.
  A temporary tunnel gets a generated ID and disappears when you stop hosting.

Append `/mcp` to the URL it prints, then point Step 3 at it. Only the URL
changes — `MCP_SERVER_LABEL` stays whatever Step 3 set:

```env
# .env
MCP_SERVER_URL=https://<generated-id>-8931.<region>.devtunnels.ms/mcp
```

```bash
cd "${WORKSHOP_RESOURCE_PREFIX}-travel-buddy"
azd env set MCP_SERVER_URL "$MCP_SERVER_URL"
```

`azd` reads its own environment, not the repo's `.env`, so skipping the
`azd env set` leaves your agent calling the real OctoTrip server. Step 3 already
wired both names into `agent.manifest.yaml` under `template.environment_variables`,
so nothing to change there — just restart `azd ai agent run` (or redeploy) so the
agent picks up the new URL.

The URL changes every time you host a fresh temporary tunnel, so redo those two
`azd env set` commands whenever you restart it. And remember that anonymous means
anonymous: anyone with the URL can call it while it's up. That's fine here — see
the disclaimer below for why, and for where the same pattern would be a breach.

In a Codespace or a VS Code dev container you can skip the CLI: forward port 8931
in the **Ports** panel and set its visibility to **Public**, which gives you an
equivalent URL.

## Disclaimer: anonymous is safe *here*, not in general

Both paths in this README — the `--allow-anonymous` dev tunnel and the
`"Anonymous"` Functions setting — publish an MCP endpoint that authenticates
nobody. That is a deliberate, bounded choice for **this** server, and it rests
entirely on what the server is:

- it holds no data and reads no data source;
- it has no managed identity and no data-plane role, so there is nothing for a
  caller to borrow;
- every response is invented from the request, so the worst a stranger can do is
  make up their own fake flights.

**Do not carry this over to a RAG endpoint.** In Step 5 you put your destinations
index behind an Azure AI Search MCP surface, and that server *does* hold a
data-plane role (`Search Index Data Reader`) against a real index. An MCP server
that authenticates nobody while holding a role that reads your data is a
[confused deputy](https://en.wikipedia.org/wiki/Confused_deputy_problem): making
it anonymous publishes read access to everything in the index — every document,
to anyone who finds the URL, with no sign-in, no audit trail, and no way to
revoke one caller. Prompt-injected or scraped content in that index leaks the
same way.

So a retrieval endpoint must authenticate its callers with Entra ID and
authorize them, exactly as [Step 5](../../docs/steps/05-rag.md) requires — and
that applies just as much to a dev tunnel in front of a local RAG server as to a
deployed one. `devtunnel host` **without** `--allow-anonymous` requires a tunnel
token and is the right default for anything that touches real data.

The rule of thumb: anonymous is only ever acceptable when the endpoint has no
data and no privileges to lend. This mock qualifies. Almost nothing else you
build in this workshop does.

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
  --assignee-object-id "$PRINCIPAL" \
  --assignee-principal-type ServicePrincipal \
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
Consumption. `--assignee-object-id` avoids the Microsoft Graph lookup that plain
`--assignee` needs — the same reason Step 2 uses it. Delete the resource group
when the workshop is over.

### No key needed — but verify it

By default an MCP tool trigger is protected: the endpoint demands the system key
named `mcp_extension`, as `?code=<key>` or an `x-functions-key` header. This
mock's `host.json` opts out with
`extensions.mcp.system.webhookAuthorizationLevel: "Anonymous"`, so the deployed
URL takes no key at all — same as the public server it replaces. (The `system`
nesting is required; a top-level `webhookAuthorizationLevel` under `mcp` is
silently ignored.)

That's safe here for the reasons in the
[disclaimer above](#disclaimer-anonymous-is-safe-here-not-in-general) — and for
the same reasons it must not be copied onto a Functions app that fronts an index,
a database, or any other real data source.

Check that it took effect before wiring up your agent — an unauthenticated
`initialize` should come back with a result, not a 401:

```bash
curl -si -X POST https://$APP.azurewebsites.net/runtime/webhooks/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",
       "capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' | head -n 1
```

A **401** means your Functions host predates the fix for
[azure-functions-mcp-extension#138](https://github.com/Azure/azure-functions-mcp-extension/issues/138),
where `Anonymous` was ignored on host runtimes older than 4.1045.0. Check the
version under **Diagnose and solve problems → Functions Host** in the portal and
redeploy — Flex Consumption moves apps onto newer hosts automatically.

Don't paper over it with the system key. Wiring `x-functions-key` into the agent
means a shared secret in your environment and your manifest, which is exactly
what this workshop avoids everywhere else; the dev tunnel above gets you a
working anonymous endpoint in the meantime.

Local runs never ask for a key, whichever level you set.

Point Step 3 at the deployed app — `.env` and the azd environment, the same two
places as the tunnel, and again only the URL changes:

```env
# .env
MCP_SERVER_URL=https://<app>.azurewebsites.net/runtime/webhooks/mcp
```

```bash
cd "${WORKSHOP_RESOURCE_PREFIX}-travel-buddy"
azd env set MCP_SERVER_URL "$MCP_SERVER_URL"
```

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
