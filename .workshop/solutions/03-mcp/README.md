> ⚠️ This is a reference solution. Workshop participants should follow the steps in the root README and only consult this if stuck.

# Solution 03 — TravelBuddy with MCP

Keeps the Step 2 function tools (`get_weather`, `get_local_time`, `convert_currency`) and adds an OctoTrip Flights MCP connection for live flight search. Point `MCP_SERVER_URL` at a different MCP server if you prefer.

## Run it

1. Copy `.env.example` to `.env` and fill in values (including `MCP_SERVER_LABEL` and `MCP_SERVER_URL`).
2. Sign in with Entra ID: `az login`.
3. Install dependencies: `pip install -r requirements.txt`.
4. Start the hosted Responses server: `python main.py`.

Try: `Find flights from Seattle (SEA) to Tokyo (NRT). List a few options with airline, price, and times.`

Back to workshop: [../../README.md](../../README.md)

## TODOs

None.

## Upstream attribution

Adapted from [03-mcp](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/03-mcp) in `microsoft-foundry/foundry-samples`.
