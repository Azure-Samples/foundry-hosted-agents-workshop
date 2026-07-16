# travel_assistant/coordinator.py
import asyncio
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    FileSkill,
    FileSkillScript,
    Skill,
    SkillScript,
    SkillsProvider,
)
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import GroupChatBuilder
from agent_framework_foundry_hosting import FoundryToolbox
from azure.ai.projects.aio import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)

logger = logging.getLogger(__name__)


# In Steps 8-9 the workflow's finalize_itinerary node owns the final deliverable
# (travel-guide PDF + response-guardrails); see FINALIZE_INSTRUCTIONS in workflow.py.
# The Coordinator here is the group chat MANAGER, used only by the legacy runtime
# group chat (build_travel_coordinator, kept for reference) — main.py hosts the
# workflow instead. As the manager it returns a structured routing decision, so it
# carries no tools and no context providers.
COORDINATOR_INSTRUCTIONS = """You are TravelBuddy's Coordinator — the manager of a group chat between three specialists (FlightsSpecialist, HotelsSpecialist, ActivitiesSpecialist). Read the traveler's request and the conversation so far, then decide who should speak next, or whether the plan is complete.

Routing:
- FlightsSpecialist: flight timing, airports, routes, layovers, weather risk, arrival windows, and fare-related currency questions.
- HotelsSpecialist: lodging areas, budgets, amenities, and neighbourhood trade-offs.
- ActivitiesSpecialist: experiences, day trips, destination guidance, and day-by-day itineraries.

Managing the conversation:
- Pick the ONE specialist who owns the next missing piece of the answer, and let each specialist finish before you choose the next one.
- For a complete trip plan, gather flight and hotel details first, then choose ActivitiesSpecialist LAST so it folds everything into the itinerary.
- Terminate once the traveler's request is fully answered. When you terminate, write the final answer for the traveler: present the complete plan drawn from the specialists' contributions.
- If a required detail is missing and blocks progress, terminate and ask the traveler that one question directly as the final answer, rather than looping a specialist.
- You never call tools yourself — only the specialists do. You route and synthesize."""

FLIGHTS_INSTRUCTIONS = """You are the Flights specialist for TravelBuddy.

Scope:
- Compare flight timing, routing, nearby airports, layovers, and arrival windows.
- Always report concrete fares/prices for the flights you recommend, and convert them to the traveler's currency when asked.

Tools (always use these rather than answering from memory):
- Flight search in the toolbox for real routes, times, and fares. If no departure date is given, call get_local_time first and use the date part of its iso_time as today's date.
- get_weather when travel timing or disruption risk matters.
- convert_currency when the traveler gives or asks for prices in another currency.

Boundaries:
- Do not choose hotels or activities.
- Cover only the flight part, then stop — do not assemble the complete trip plan. Return your findings for the orchestrator that called you; it decides what runs next and writes the final answer for the traveler, so don't address the traveler directly. If a detail you need is missing, say what's missing instead of guessing."""

HOTELS_INSTRUCTIONS = """You are the Hotels specialist for TravelBuddy.

Scope:
- Recommend neighbourhoods and lodging trade-offs.
- Respect budget, dates, accessibility, room type, and must-have amenities.

Tools (always use these rather than answering from memory):
- Grounded destination knowledge (the destinations index) before recommending neighbourhoods or areas.
- The toolbox's web search for current rates, availability signals, and source-backed lodging guidance.
- convert_currency for nightly budgets and total-stay estimates.

Boundaries:
- Do not invent live availability.
- Do not plan full-day activities unless they affect neighbourhood choice.
- Cover only the lodging part, then stop — do not assemble the complete trip plan. Return your findings for the orchestrator that called you; it decides what runs next and writes the final answer for the traveler, so don't address the traveler directly. If a detail you need is missing, say what's missing instead of guessing."""

ACTIVITIES_INSTRUCTIONS = """You are the Activities specialist for TravelBuddy.

Scope:
- Suggest experiences, day trips, food areas, museum days, outdoor options, and rainy-day alternatives.

Tools (always use these rather than answering from memory):
- Grounded destination knowledge (the destinations index) before making specific recommendations.
- The toolbox's web search for current events, advisories, and source-backed guidance.

Boundaries:
- Do not choose flights or hotels.
- Cover only the activities part, then stop — do not assemble the complete trip plan. Return your findings for the orchestrator that called you; it decides what runs next and writes the final answer for the traveler, so don't address the traveler directly. If the itinerary needs a flight or hotel detail that isn't available yet, say what's missing instead of guessing."""


def run_local_skill_script(
    skill: Skill, script: SkillScript, args: dict[str, Any] | list[str] | None = None
) -> str:
    """Run a trusted file-based skill script with positional CLI arguments."""
    if not isinstance(skill, FileSkill) or not isinstance(script, FileSkillScript):
        return "Error: only file-based skill scripts can be run by this runner."

    skill_path = Path(skill.path).resolve()
    script_path = Path(script.full_path).resolve()
    if skill_path != script_path and skill_path not in script_path.parents:
        return f"Error: script '{script.name}' resolves outside the skill directory."

    command = [sys.executable, str(script_path)]
    if isinstance(args, list):
        for item in args:
            if not isinstance(item, str):
                return (
                    f"Error: script '{script.name}' only accepts string CLI arguments, "
                    f"but received a {type(item).__name__}."
                )
        command.extend(args)
    elif args is not None:
        return (
            f"Error: script '{script.name}' expects positional CLI arguments as a list "
            f"of strings, but received {type(args).__name__}."
        )

    try:
        completed = subprocess.run(
            command, cwd=skill_path, capture_output=True, check=False, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return f"Error: script '{script.name}' timed out after 60 seconds."

    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "no error output was produced."
        return f"Error: script '{script.name}' failed with exit code {completed.returncode}: {details}"
    return completed.stdout.strip() or f"Script '{script.name}' completed successfully."


class TrustedSkillsProvider(SkillsProvider):
    """A ``SkillsProvider`` that runs its skill tools without an approval gate.

    agent-framework registers every skill tool (``load_skill``,
    ``read_skill_resource``, ``run_skill_script``) with
    ``approval_mode="always_require"``. The documented opt-out,
    ``ToolApprovalMiddleware``, needs an ``AgentSession``, which the hosted
    ``ResponsesHostServer`` does not provide -- so an unattended run would stall
    on an approval request. These skills are authored in this repo (and the
    trusted runner is armed for local skills only), so we register their tools
    without the gate.

    Workshop shortcut, not a production pattern: disabling approval lets the
    hosted agent run unattended, but it trades away the human review that guards
    ``run_skill_script`` from executing untrusted code. In production, keep the
    gate and run the agent in a client flow that supplies an ``AgentSession`` so
    each script call can be approved by a human (or a policy). Use
    ``never_require`` only for skills whose provenance you fully control.
    """

    def _create_tools(self, skills):
        tools = super()._create_tools(skills)
        for tool in tools:
            tool.approval_mode = "never_require"
        return tools


def _build_search_provider(credential) -> AzureAISearchContextProvider:
    endpoint = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
    index_name = os.environ["AZURE_AI_SEARCH_INDEX_NAME"]
    return AzureAISearchContextProvider(
        source_id="travelbuddy_destinations",
        endpoint=endpoint,
        index_name=index_name,
        credential=credential,
        mode="semantic",
        top_k=3,
    )


LOCAL_SKILLS_DIR = Path(__file__).parent / "skills"
# The deployed container's app directory is read-only, so download into the OS
# temp dir (writable both locally and in the hosted container).
FOUNDRY_DOWNLOADED_SKILLS_DIR = Path(tempfile.gettempdir()) / "foundry_downloaded_skills"
SKILL_DOWNLOAD_TIMEOUT_SECONDS = 60.0


def _foundry_skill_names() -> list[str]:
    """Parse FOUNDRY_SKILL_NAMES, treating an unresolved ${VAR}/{{VAR}} as empty."""
    raw = os.environ.get("FOUNDRY_SKILL_NAMES", "").strip()
    if (raw.startswith("${") and raw.endswith("}")) or (raw.startswith("{{") and raw.endswith("}}")):
        raw = ""
    parsed = [name.strip().strip('"').strip("'") for name in raw.split(",")]
    return [name for name in parsed if name]


def _safe_extract_zip(zf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Unpack a downloaded skill archive, rejecting entries that escape dest_dir (zip-slip guard)."""
    dest_root = dest_dir.resolve()
    for member in zf.infolist():
        target = (dest_root / member.filename).resolve()
        if dest_root != target and dest_root not in target.parents:
            raise RuntimeError(f"Refusing unsafe zip entry '{member.filename}'.")
    zf.extractall(dest_dir)


async def _download_foundry_skills(endpoint: str, names: list[str]) -> None:
    """Download each named Foundry skill into the temp foundry_downloaded_skills/<name>/ cache."""
    if FOUNDRY_DOWNLOADED_SKILLS_DIR.exists():
        shutil.rmtree(FOUNDRY_DOWNLOADED_SKILLS_DIR)
    FOUNDRY_DOWNLOADED_SKILLS_DIR.mkdir(parents=True)
    async with (
        AsyncDefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project,
    ):
        for name in names:
            stream = await project.beta.skills.download(name)
            data = b"".join([chunk async for chunk in stream])
            skill_dir = FOUNDRY_DOWNLOADED_SKILLS_DIR / name
            skill_dir.mkdir()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                _safe_extract_zip(zf, skill_dir)
            if not (skill_dir / "SKILL.md").is_file():
                raise RuntimeError(f"Foundry skill '{name}' has no SKILL.md at its archive root.")


def _build_skills_provider() -> TrustedSkillsProvider:
    """Download the required Foundry skill(s), then serve them and the local skill from ONE provider.

    The local travel-guide skill needs the trusted ``run_local_skill_script`` runner to
    execute create_travel_guide.py. Both folders share one ``from_paths`` so their skill
    tools never collide, but a ``script_filter`` arms the runner for local skills only, so a
    downloaded (remote) skill can never execute local code even if it shipped a script.
    """
    names = _foundry_skill_names()
    if not names:
        raise RuntimeError(
            "FOUNDRY_SKILL_NAMES is empty. Upload the Foundry skill once with "
            "`python foundry_skills/provision_skills.py`, then set "
            'FOUNDRY_SKILL_NAMES=response-guardrails so the agent can download it at startup.'
        )
    asyncio.run(
        asyncio.wait_for(
            _download_foundry_skills(os.environ["AZURE_AI_PROJECT_ENDPOINT"], names),
            timeout=SKILL_DOWNLOAD_TIMEOUT_SECONDS,
        )
    )
    downloaded_names = set(names)
    return TrustedSkillsProvider.from_paths(
        [LOCAL_SKILLS_DIR, FOUNDRY_DOWNLOADED_SKILLS_DIR],
        script_runner=run_local_skill_script,
        # Trusted runner is armed for local skills only; a downloaded Foundry skill
        # (matched by name) can never run a script even if its archive shipped one.
        script_filter=lambda skill_name, _path: skill_name not in downloaded_names,
    )


def make_client(credential=None) -> FoundryChatClient:
    """Create the shared Foundry chat client used by every specialist."""
    return FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential or DefaultAzureCredential(),
    )


# --- Specialist factories -------------------------------------------------
# Extracted in Step 8 so the runtime Coordinator (this file) and the durable
# workflow (workflow.py) build the *same* specialists from one source of truth.


# --- Specialist factories -------------------------------------------------
# One source of truth for both the workflow (workflow.py) and the reference
# Coordinator below. Each specialist keeps its Step 7 `description=`: the group
# chat manager routes on it, while the workflow ignores it (its node edges are
# explicit) — kept so the extracted factories stay behavior-identical to Step 7.


def create_flights_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Flights: weather + local time + currency, plus the toolbox (OctoTrip MCP is flight search)."""
    credential = credential or DefaultAzureCredential()
    toolbox = FoundryToolbox(credential)
    return Agent(
        client=client,
        name="FlightsSpecialist",
        description="Handles flight timing, routing, airport, weather-risk, and currency questions.",
        instructions=FLIGHTS_INSTRUCTIONS,
        tools=[get_weather, get_local_time, convert_currency, toolbox],
        default_options={"store": False},
    )


def create_hotels_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Hotels: currency + web search (toolbox) + grounded destination knowledge (RAG)."""
    credential = credential or DefaultAzureCredential()
    toolbox = FoundryToolbox(credential)
    search = _build_search_provider(credential)
    return Agent(
        client=client,
        name="HotelsSpecialist",
        description="Handles hotel area, budget, amenity, and lodging trade-off questions.",
        instructions=HOTELS_INSTRUCTIONS,
        tools=[convert_currency, toolbox],
        context_providers=[search],
        default_options={"store": False},
    )


def create_activities_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Activities: toolbox (web/reference) + grounded destination knowledge (RAG)."""
    credential = credential or DefaultAzureCredential()
    toolbox = FoundryToolbox(credential)
    search = _build_search_provider(credential)
    return Agent(
        client=client,
        name="ActivitiesSpecialist",
        description="Handles experiences, day trips, local guidance, and itinerary-building questions.",
        instructions=ACTIVITIES_INSTRUCTIONS,
        tools=[toolbox],
        context_providers=[search],
        default_options={"store": False},
    )


def build_travel_coordinator() -> Agent:
    """Legacy Step 7-style group chat Coordinator, kept as reference only.

    Steps 8-9 host the workflow (see workflow.py / main.py), not this factory. It
    has no deliverable owner: the travel-guide PDF and response-guardrails skills
    live on the workflow's finalize node, not here. Do not use this as the Step 8/9
    runtime path.
    """
    credential = DefaultAzureCredential()
    client = make_client(credential)

    # The Coordinator is the group chat MANAGER (orchestrator_agent): each round it
    # returns a structured next-speaker/terminate decision, so it has no tools and no
    # context providers. In Steps 8-9 the deliverable (travel-guide PDF +
    # response-guardrails) lives on the workflow's finalize node instead.
    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        default_options={"store": False},
    )
    flights = create_flights_agent(client, credential)
    hotels = create_hotels_agent(client, credential)
    activities = create_activities_agent(client, credential)

    # GroupChatBuilder wires the manager to each participant: every round the
    # Coordinator picks the next specialist or terminates with the final answer, then
    # the workflow completes (IDLE) — it never parks on a request_info, so hosted
    # follow-ups just continue the conversation. max_rounds is CUMULATIVE across the
    # whole hosted conversation (the round counter is checkpoint-restored), so 40
    # leaves headroom for a multi-turn session while still stopping a manager that
    # never terminates.
    workflow = (
        GroupChatBuilder(
            participants=[flights, hotels, activities],
            orchestrator_agent=coordinator,
            max_rounds=40,
        )
        .build()
    )

    return workflow.as_agent()
