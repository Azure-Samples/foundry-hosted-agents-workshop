# travel_assistant/main.py
import logging
import os

from agent_framework import Agent
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)

logger = logging.getLogger(__name__)


def main() -> None:
    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    # FoundryToolbox reads TOOLBOX_ENDPOINT from the environment, authenticates
    # every request with the credential, and connects on first use. The toolbox
    # bundles web search, Code Interpreter, and the OctoTrip flights MCP server.
    toolbox = FoundryToolbox(credential)

    tools = [
        get_weather,
        get_local_time,
        convert_currency,
        toolbox,
    ]

    # RAG: search the destinations index before each turn and inject the top
    # matches into model context.
    search_endpoint = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
    search_index_name = os.environ["AZURE_AI_SEARCH_INDEX_NAME"]
    context_providers = [
        AzureAISearchContextProvider(
            source_id="travelbuddy_destinations",
            endpoint=search_endpoint,
            index_name=search_index_name,
            credential=credential,
            mode="semantic",
            top_k=3,
        )
    ]

    agent = Agent(
        client=client,
        name="travel-buddy",
        instructions=(
            "You are TravelBuddy, a friendly travel assistant. "
            "Give practical, concise advice for trip planning, including local context, "
            "budget awareness, and safety-minded tips. "
            "Use your tools for weather, local time, and currency conversion "
            "when the traveler asks time-sensitive questions. Keep answers brief. "
            "Use the Foundry Toolbox for flight search (when the traveler gives no "
            "departure date, call get_local_time and use the date part of its "
            "iso_time as today's date), for web search of current "
            "travel advisories and events, and for Code Interpreter to analyze an "
            "uploaded itinerary.csv (budget totals, currency conversion, charts). "
            "Use the grounded destination context when relevant; if the destinations "
            "index does not contain enough detail, say what is missing."
        ),
        tools=tools,
        context_providers=context_providers,
        default_options={"store": False},
    )

    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
