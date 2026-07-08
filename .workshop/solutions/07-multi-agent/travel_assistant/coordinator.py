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
from agent_framework.orchestrations import HandoffBuilder
from agent_framework_foundry_hosting import FoundryToolbox
from azure.ai.projects.aio import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)

logger = logging.getLogger(__name__)


# The Coordinator is a pure router/synthesizer: in a runtime handoff it issues the
# handoff tool calls and is re-invoked after each hand-back, so it CANNOT also carry a
# tool-producing context provider (the skills provider). Unlike Steps 1-6 this
# Coordinator has no tools and no context providers.
# In a runtime handoff it is the only participant invoked twice (route, then synthesize
# after a hand-back), so attaching a tool-producing context provider here (the skills
# provider) breaks the store=False history replay on that second call
# ("No tool call found for function call output"). The final deliverable — the
# travel-guide PDF and the response-guardrails check — therefore rides on the Activities
# specialist (a leaf, invoked once). Step 8's workflow adds a dedicated finalize node
# that CAN own the deliverable. See the Step 7 doc for the full explanation.
COORDINATOR_INSTRUCTIONS = """You are TravelBuddy's Coordinator. Understand the traveler's request, route specialist work to the right agent, and synthesize a clear final answer.

Routing:
- FlightsSpecialist: flight timing, airports, routes, layovers, weather risk, arrival windows, and fare-related currency questions.
- HotelsSpecialist: lodging areas, budgets, amenities, and neighbourhood trade-offs.
- ActivitiesSpecialist: experiences, day trips, destination guidance, day-by-day itineraries, and the downloadable PDF trip guide.
- For a complete trip plan, gather flight and hotel details first, then hand to ActivitiesSpecialist LAST with the full draft so it produces the final PDF trip guide and runs the response-guardrails check. Return that guarded result as your answer without rewriting it.

You are the only agent who talks to the traveler: specialists hand their work back to you, so when one hands back because a required detail is missing, ask the traveler yourself rather than routing to that specialist again.
Ask a clarifying question only when a missing detail blocks the next useful step, and keep the traveler informed when you route work to a specialist."""

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
- Always hand back to the Coordinator when you finish your part, when the request turns to lodging, experiences, or the complete plan, or when a missing detail blocks your specialist work. The Coordinator is the only agent that talks to the traveler, so never ask the traveler directly; hand back and let the Coordinator relay any question."""

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
- Always hand back to the Coordinator when you finish your part, when the request turns to flights, activities, or a complete itinerary, or when a missing detail blocks your specialist work. The Coordinator is the only agent that talks to the traveler, so never ask the traveler directly; hand back and let the Coordinator relay any question."""

# Activities owns the final deliverable in Step 7 (see the Coordinator note above): the
# LOCAL travel-guide skill (always present) renders the PDF trip guide, and the FOUNDRY
# response-guardrails skill checks the answer. If you skipped the Foundry skill in Step 6,
# drop the response-guardrails line below and serve only the local skill — see the Step 7 doc.
ACTIVITIES_INSTRUCTIONS = """You are the Activities specialist for TravelBuddy.

Scope:
- Suggest experiences, day trips, food areas, museum days, outdoor options, and rainy-day alternatives.
- Produce the trip's downloadable, shareable PDF guide once the plan is clear.

Tools (always use these rather than answering from memory):
- Grounded destination knowledge (the destinations index) before making specific recommendations.
- The toolbox's web search for current events, advisories, and source-backed guidance.

Skills (always use these):
- Use the travel-guide skill to turn the plan into a downloadable, shareable PDF trip guide.
- Apply the response-guardrails skill to every response you produce before handing back.

Boundaries:
- Do not choose flights or hotels.
- Always hand back to the Coordinator when you finish your part, when the itinerary needs flight or hotel constraints, or when a missing detail blocks your specialist work. The Coordinator is the only agent that talks to the traveler, so never ask the traveler directly; hand back and let the Coordinator relay any question."""


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


def build_travel_coordinator() -> Agent:
    """Build the Coordinator + specialists handoff and expose it as a single agent."""
    credential = DefaultAzureCredential()
    client = FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    # Carried capabilities from Steps 4-6, wired per agent below. The skills provider
    # (LOCAL travel-guide + the FOUNDRY response-guardrails skill downloaded at
    # startup, see _build_skills_provider) rides on the Activities leaf specialist —
    # the handoff Coordinator can't carry a context provider (see COORDINATOR_INSTRUCTIONS).
    toolbox = FoundryToolbox(credential)
    search = _build_search_provider(credential)
    skills = _build_skills_provider()

    # HandoffBuilder short-circuits tool calls during a handoff, so every participant
    # must set require_per_service_call_history_persistence=True or build() raises.
    # The Coordinator is a pure router/synthesizer: no tools, no context providers.
    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )

    # Flights: weather + local time + currency, plus the toolbox (OctoTrip MCP is flight search).
    flights = Agent(
        client=client,
        name="FlightsSpecialist",
        instructions=FLIGHTS_INSTRUCTIONS,
        tools=[get_weather, get_local_time, convert_currency, toolbox],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )

    # Hotels: currency + web search (toolbox) + grounded destination knowledge (RAG).
    hotels = Agent(
        client=client,
        name="HotelsSpecialist",
        instructions=HOTELS_INSTRUCTIONS,
        tools=[convert_currency, toolbox],
        context_providers=[search],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )

    # Activities: toolbox (web/reference) + grounded destination knowledge (RAG) +
    # the skills provider, so this leaf owns the travel-guide PDF and response-guardrails.
    activities = Agent(
        client=client,
        name="ActivitiesSpecialist",
        instructions=ACTIVITIES_INSTRUCTIONS,
        tools=[toolbox],
        context_providers=[search, skills],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )

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
