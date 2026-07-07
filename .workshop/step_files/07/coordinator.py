"""TravelBuddy multi-agent Coordinator — build the handoff graph.

Fill in the TODOs below following docs/steps/07-multi-agent.md.

The per-specialist ``agents/*/agent.yaml`` + ``agent.manifest.yaml`` slices
*describe* each specialist's role and capability boundary, but nothing loads
them at runtime. **This file is the executable source of truth**: the YAML
``instructions:`` become the string constants below, and the tool/RAG/skill
lists become the hand-written ``tools=[...]`` / ``context_providers=[...]``
arguments. Keep the two in sync.

Stuck? The complete, runnable version lives at
.workshop/solutions/07-multi-agent/travel_assistant/coordinator.py — including
the full skills provider that also downloads the Foundry response-guardrails
skill from the project at startup.
"""

from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import HandoffBuilder
from agent_framework_foundry_hosting import FoundryToolbox
from azure.identity import DefaultAzureCredential

from tools import convert_currency, get_local_time, get_weather

# TODO: carry run_local_skill_script over from your Step 6 main.py so the
# Activities specialist can run the local travel-guide skill (and build the
# SkillsProvider that serves it alongside the Foundry response-guardrails skill).


# --- Instruction constants --------------------------------------------------
# These are what the runtime actually uses. The matching agents/*/agent.yaml
# `instructions:` blocks are documentation — keep them aligned with these.
COORDINATOR_INSTRUCTIONS = ""  # TODO: understand the request, route to a specialist, synthesize the answer.
FLIGHTS_INSTRUCTIONS = ""      # TODO: flights only; hand back for lodging, activities, or the full plan.
HOTELS_INSTRUCTIONS = ""       # TODO: lodging only; use destinations RAG + currency.
ACTIVITIES_INSTRUCTIONS = ""   # TODO: experiences, day trips, itinerary; use toolbox + RAG + the travel-guide skill.


def _build_search_provider(credential) -> AzureAISearchContextProvider:
    """Carried from Step 5 — the destinations RAG context provider."""
    # TODO: return the Step 5 AzureAISearchContextProvider
    # (AZURE_AI_SEARCH_ENDPOINT / AZURE_AI_SEARCH_INDEX_NAME, mode="semantic", top_k=3).
    raise NotImplementedError("TODO: build the destinations RAG provider (see Step 5).")


def build_travel_coordinator() -> Agent:
    """Build the Coordinator + specialists handoff and expose it as one agent."""
    credential = DefaultAzureCredential()
    client = FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    # Carried capabilities from Steps 4–6, sliced per specialist below.
    toolbox = FoundryToolbox(credential)
    search = _build_search_provider(credential)
    # TODO: build the skills provider (local travel-guide + Foundry response-guardrails).
    # skills = ...

    # Every participant MUST set require_per_service_call_history_persistence=True.
    # A handoff fires mid-turn (before the handoff tool call resolves), so without
    # the flag that in-flight call is dropped and HandoffBuilder.build() raises
    # ValueError. Set it on the Coordinator and all three specialists.
    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        require_per_service_call_history_persistence=True,
    )

    # TODO: build the three specialists with their sliced capabilities, e.g.:
    #   flights = Agent(
    #       client=client, name="FlightsSpecialist", instructions=FLIGHTS_INSTRUCTIONS,
    #       tools=[get_weather, get_local_time, convert_currency, toolbox],
    #       require_per_service_call_history_persistence=True,
    #   )
    #   hotels = Agent(... tools=[convert_currency], context_providers=[search] ...)
    #   activities = Agent(... tools=[toolbox], context_providers=[search, skills] ...)

    # TODO: wire the handoff graph and expose it as a single agent:
    #   workflow = (
    #       HandoffBuilder(
    #           name="travelbuddy-runtime-handoff",
    #           participants=[coordinator, flights, hotels, activities],
    #       )
    #       .with_start_agent(coordinator)
    #       .add_handoff(coordinator, [flights, hotels, activities])
    #       .add_handoff(flights, [coordinator])
    #       .add_handoff(hotels, [coordinator])
    #       .add_handoff(activities, [coordinator])
    #       .build()
    #   )
    #   return workflow.as_agent()
    raise NotImplementedError(
        "TODO: build the specialists and handoff graph per docs/steps/07-multi-agent.md"
    )


async def run_once(prompt: str):
    coordinator = build_travel_coordinator()
    return await coordinator.run(prompt)
