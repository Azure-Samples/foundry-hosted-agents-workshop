"""TravelBuddy multi-agent Coordinator — build the handoff graph.

Fill in the TODOs below by reading the delivered slices, following
docs/steps/07-multi-agent.md.

The per-specialist ``agents/<name>/agent.yaml`` + ``agent.manifest.yaml`` slices
are already written for you and *describe* each specialist's role and capability
boundary, but nothing loads them at runtime. **This file is the executable
source of truth** — you translate each slice into code here:

- ``agents/<name>/agent.yaml`` -> the ``*_INSTRUCTIONS`` constant below
  (its ``instructions:`` block).
- ``agents/<name>/agent.manifest.yaml`` -> that specialist's ``tools=[...]`` and
  ``context_providers=[...]`` arguments (its ``tools`` / ``rag`` / ``skills``).

Keep the two in sync: the slice is the reviewable contract, this file is what runs.

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
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

# main.py collapses to hosting the Coordinator this step, so load .env here
# (carry this over from your Step 6 main.py).
load_dotenv(override=True)

# TODO: carry run_local_skill_script over from your Step 6 main.py so the
# Coordinator can run the local travel-guide skill (it renders the final PDF trip
# guide), and build the SkillsProvider that serves it. If you built the Foundry
# response-guardrails skill in Step 6, serve it here too so the Coordinator can
# guardrail its final answer; if you skipped it (no public-network Foundry
# project), serve only the local skill and drop the response-guardrails line from
# COORDINATOR_INSTRUCTIONS.


# --- Instruction constants --------------------------------------------------
# The three specialist constants come straight from agents/<name>/agent.yaml.
# The Coordinator has NO slice — you write it. These are what the runtime uses;
# keep the specialist ones aligned with their slices.
#
# TODO: write COORDINATOR_INSTRUCTIONS (the router's brief). Cover:
#   - Role: understand the request, route to the right specialist, synthesize one answer.
#   - Routing rules (one line each, matching each slice's `description`):
#       Flights -> timing, airports, routes, layovers, weather risk, fares
#       Hotels  -> lodging areas, budgets, amenities, neighbourhood trade-offs
#       Activities -> experiences, day trips, destination guidance, itineraries
#   - Full trip: hand off to each relevant specialist, then reconcile into one plan.
#   - Final deliverable (you own it): always use the travel-guide skill to render
#     the plan as a downloadable PDF, and always apply the response-guardrails
#     skill to your answer before returning it (drop that line if you skipped the
#     Foundry skill in Step 6).
#   - You are the only agent who talks to the traveler: when a specialist hands
#     back because a detail is missing, ask the traveler yourself rather than
#     routing straight back to that specialist (which can loop).
#   - Ask a clarifying question only when a missing detail blocks the next step;
#     keep the traveler informed when routing.
COORDINATOR_INSTRUCTIONS = ""
FLIGHTS_INSTRUCTIONS = ""      # TODO: from agents/flights/agent.yaml — flights only; hand back for lodging/activities/full plan.
HOTELS_INSTRUCTIONS = ""       # TODO: from agents/hotels/agent.yaml — lodging only; use destinations RAG + toolbox web search + currency.
ACTIVITIES_INSTRUCTIONS = ""   # TODO: from agents/activities/agent.yaml — experiences, day trips, itinerary; toolbox web search + destinations RAG.


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
    # TODO: build the skills provider from your Step 6 code (local travel-guide,
    # plus the Foundry response-guardrails skill only if you built it in Step 6).
    # It belongs to the Coordinator now — it owns the final PDF guide + guardrails.
    # skills = ...

    # Every participant MUST set require_per_service_call_history_persistence=True.
    # A handoff fires mid-turn (before the handoff tool call resolves), so without
    # the flag that in-flight call is dropped and HandoffBuilder.build() raises
    # ValueError. Set it on the Coordinator and all three specialists.
    # Carry default_options={"store": False} over from every earlier step: the
    # hosting layer manages history, so don't persist responses server-side. Set
    # it on the Coordinator and all three specialists below.
    # TODO: once you build the skills provider above, attach it to the Coordinator
    # with context_providers=[skills] — the Coordinator owns the final PDF guide
    # and the response-guardrails check.
    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )

    # TODO: build the three specialists. Read each agents/<name>/agent.manifest.yaml
    # to see the exact capability slice, then translate it to Agent(...) arguments:
    #   - `tools:`  -> tools=[...]              (function tools + the toolbox)
    #   - `rag:`    -> context_providers=[search]   (the destinations index)
    #   (the travel-guide / response-guardrails skills are the Coordinator's, above)
    # e.g.:
    #   flights = Agent(
    #       client=client, name="FlightsSpecialist", instructions=FLIGHTS_INSTRUCTIONS,
    #       tools=[get_weather, get_local_time, convert_currency, toolbox],
    #       require_per_service_call_history_persistence=True,
    #       default_options={"store": False},
    #   )
    #   hotels = Agent(... tools=[convert_currency, toolbox], context_providers=[search],
    #                  require_per_service_call_history_persistence=True,
    #                  default_options={"store": False})
    #   activities = Agent(... tools=[toolbox], context_providers=[search],
    #                      require_per_service_call_history_persistence=True,
    #                      default_options={"store": False})

    # The handoff graph is wired for you and exposed as a single agent. It refers
    # to flights/hotels/activities, so define those three specialists above first
    # (and fill in _build_search_provider) before this runs.
    workflow = (
        HandoffBuilder(
            name="travelbuddy-runtime-handoff",
            participants=[coordinator, flights, hotels, activities],
        )
        .with_start_agent(coordinator)
        .add_handoff(coordinator, [flights, hotels, activities])
        .add_handoff(flights, [coordinator])
        .add_handoff(hotels, [coordinator])
        .add_handoff(activities, [coordinator])
        .build()
    )
    return workflow.as_agent()
