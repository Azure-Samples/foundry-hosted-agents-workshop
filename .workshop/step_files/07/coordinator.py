"""TravelBuddy multi-agent Coordinator — build the group chat.

Fill in the TODOs below by reading the delivered slices, following
docs/steps/07-multi-agent.md.

The per-specialist ``agents/<name>/agent.yaml`` + ``agent.manifest.yaml`` slices
are already written for you and *describe* each specialist's role and capability
boundary, but nothing loads them at runtime. **This file is the executable
source of truth** — you translate each slice into code here:

- ``agents/<name>/agent.yaml`` -> the ``*_INSTRUCTIONS`` constant below
  (its ``instructions:`` block) and the specialist's ``description=`` argument
  (its ``description:`` line).
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
from agent_framework.orchestrations import GroupChatBuilder
from agent_framework_foundry_hosting import FoundryToolbox
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

# main.py collapses to hosting the Coordinator this step, so load .env here
# (carry this over from your Step 6 main.py).
load_dotenv(override=True)

# TODO: carry run_local_skill_script over from your Step 6 main.py so the
# Activities specialist can run the local travel-guide skill (it renders the final
# PDF trip guide), and build the SkillsProvider that serves it. If you built the
# Foundry response-guardrails skill in Step 6, serve it here too so Activities can
# guardrail its answer; if you skipped it (no public-network Foundry project), serve
# only the local skill and drop the response-guardrails line from ACTIVITIES_INSTRUCTIONS.
# Why Activities and not the Coordinator? The Coordinator is the group chat MANAGER: it
# returns a structured next-speaker/terminate decision each round, so a skill it carried
# wouldn't reliably fire. The skills therefore ride on the Activities participant.
# Step 8's workflow adds a dedicated finalize node that owns the deliverable outright.


# --- Instruction constants --------------------------------------------------
# The three specialist constants come straight from agents/<name>/agent.yaml.
# The Coordinator has NO slice — you write it. These are what the runtime uses;
# keep the specialist ones aligned with their slices.
#
# TODO: write COORDINATOR_INSTRUCTIONS (the manager's brief). Cover:
#   - Role: you are the manager of a group chat between the three specialists —
#     read the conversation, pick who speaks next, or terminate with the final answer.
#   - Routing rules (one line each, matching each slice's `description`):
#       Flights -> timing, airports, routes, layovers, weather risk, fares
#       Hotels  -> lodging areas, budgets, amenities, neighbourhood trade-offs
#       Activities -> experiences, day trips, destination guidance, itineraries, PDF guide
#   - Full trip: gather flights and hotels first, then choose Activities LAST so it
#     folds everything into the itinerary, produces the PDF, and runs the guardrails check.
#   - Terminate when the request is fully answered, and when you do, write the final
#     answer for the traveler — include the Activities guarded guide and its PDF link
#     verbatim (don't rewrite or drop them).
#   - If a blocking detail is missing, terminate and ask the traveler that one question
#     directly, rather than looping a specialist.
#   - You never call tools yourself — only the specialists do; you route and synthesize.
#   (The Coordinator is the manager — the travel-guide PDF and response-guardrails
#    ride on the Activities specialist, not here; see the skills TODO above.)
COORDINATOR_INSTRUCTIONS = ""
FLIGHTS_INSTRUCTIONS = ""      # TODO: from agents/flights/agent.yaml — flights only; report findings, then stop.
HOTELS_INSTRUCTIONS = ""       # TODO: from agents/hotels/agent.yaml — lodging only; use destinations RAG + toolbox web search + currency.
ACTIVITIES_INSTRUCTIONS = ""   # TODO: from agents/activities/agent.yaml — experiences, day trips, itinerary; toolbox web search + destinations RAG + the travel-guide/response-guardrails skills.


def _build_search_provider(credential) -> AzureAISearchContextProvider:
    """Carried from Step 5 — the destinations RAG context provider."""
    # TODO: return the Step 5 AzureAISearchContextProvider
    # (AZURE_AI_SEARCH_ENDPOINT / AZURE_AI_SEARCH_INDEX_NAME, mode="semantic", top_k=3).
    raise NotImplementedError("TODO: build the destinations RAG provider (see Step 5).")


def build_travel_coordinator() -> Agent:
    """Build the Coordinator + specialists group chat and expose it as one agent."""
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
    # It belongs to the Activities specialist below — that participant owns the final
    # PDF guide + guardrails (a skill on the manager Coordinator wouldn't reliably fire).
    # skills = ...

    # Carry default_options={"store": False} over from every earlier step: the hosting
    # layer manages history, so don't persist responses server-side. Set it on the
    # Coordinator and all three specialists below.
    # The Coordinator is the group chat MANAGER (orchestrator_agent): it returns a
    # structured next-speaker/terminate decision each round, so it has no tools and no
    # context providers.
    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        default_options={"store": False},
    )

    # TODO: build the three specialists. Read each agents/<name>/agent.manifest.yaml
    # to see the exact capability slice, then translate it to Agent(...) arguments:
    #   - `description:` (agent.yaml) -> description=...   (feeds the manager's routing prompt)
    #   - `tools:`  -> tools=[...]                         (function tools + the toolbox)
    #   - `rag:`    -> context_providers=[search]          (the destinations index)
    #   - `skills:` -> context_providers=[search, skills]  (Activities only)
    # e.g.:
    #   flights = Agent(
    #       client=client, name="FlightsSpecialist",
    #       description="Handles flight timing, routing, airport, weather-risk, and currency questions.",
    #       instructions=FLIGHTS_INSTRUCTIONS,
    #       tools=[get_weather, get_local_time, convert_currency, toolbox],
    #       default_options={"store": False},
    #   )
    #   hotels = Agent(... tools=[convert_currency, toolbox], context_providers=[search],
    #                  default_options={"store": False})
    #   activities = Agent(... tools=[toolbox], context_providers=[search, skills],
    #                      default_options={"store": False})

    # The group chat is wired for you and exposed as a single agent. It refers to
    # flights/hotels/activities, so define those three specialists above first (and fill
    # in _build_search_provider) before this runs.
    #
    # GroupChatBuilder wires the manager (orchestrator_agent) to each participant with
    # bidirectional edges: every round the Coordinator picks the next specialist or
    # terminates with the final answer, then the workflow completes (IDLE). Because it
    # never parks on a request_info, a follow-up question in the same conversation is
    # just the next run against the restored history — no "Unexpected content type"
    # error. max_rounds caps the orchestrator rounds; the counter is checkpoint-restored,
    # so the cap is CUMULATIVE across the whole conversation, not per turn. 40 leaves
    # ample headroom for a multi-turn session while still stopping a runaway manager.
    workflow = (
        GroupChatBuilder(
            participants=[flights, hotels, activities],
            orchestrator_agent=coordinator,
            max_rounds=40,
        )
        .build()
    )
    return workflow.as_agent()
