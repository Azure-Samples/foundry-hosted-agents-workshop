# travel_assistant/main.py
import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)


def main() -> None:
    client = FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        name="travel-buddy",
        instructions=(
            "You are TravelBuddy, a friendly travel assistant. "
            "Give practical, concise advice for trip planning, including local context, "
            "budget awareness, and safety-minded tips. "
            "Use your tools for weather, local time, and currency conversion "
            "when the traveler asks time-sensitive questions. Keep answers brief."
        ),
        tools=[get_weather, get_local_time, convert_currency],
        default_options={"store": False},
    )

    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
