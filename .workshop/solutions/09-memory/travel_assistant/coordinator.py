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
from agent_framework.foundry import FoundryChatClient, FoundryMemoryProvider
from agent_framework.orchestrations import HandoffBuilder
from agent_framework_foundry_hosting import FoundryToolbox
from azure.ai.projects.aio import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)

logger = logging.getLogger(__name__)


COORDINATOR_INSTRUCTIONS = (
    "You are TravelBuddy's Coordinator. Understand the traveler's request, route "
    "specialist work to the right agent, and synthesize a clear final answer.\n"
    "Routing:\n"
    "- FlightsSpecialist: flight timing, airports, routes, layovers, weather risk, "
    "arrival windows, and fare-related currency questions.\n"
    "- HotelsSpecialist: lodging areas, budgets, amenities, and neighbourhood trade-offs.\n"
    "- ActivitiesSpecialist: experiences, day trips, destination guidance, and "
    "day-by-day itineraries.\n"
    "- For a complete trip plan, hand off to each relevant specialist, then reconcile "
    "their answers into one plan.\n"
    "Ask a clarifying question only when a missing detail blocks the next useful step, "
    "and keep the traveler informed when you route work to a specialist."
)

FLIGHTS_INSTRUCTIONS = (
    "You are FlightsSpecialist. Help with flight routes, airports, layovers, arrival "
    "windows, and weather risk, and use flight search from the toolbox when the "
    "traveler asks for specific routes; if no departure date is given, call "
    "get_local_time and use the date part of its iso_time as today's date. "
    "Use currency conversion for fares. Do not "
    "choose hotels or activities. Hand back to the Coordinator when the traveler asks "
    "about lodging, experiences, or the complete trip plan."
)

HOTELS_INSTRUCTIONS = (
    "You are HotelsSpecialist. Use grounded destination knowledge and currency "
    "conversion to recommend lodging areas and hotel trade-offs. Respect budget, "
    "dates, amenities, accessibility, and room constraints. Hand back to the "
    "Coordinator for flights, activities, or final trip synthesis."
)

ACTIVITIES_INSTRUCTIONS = (
    "You are ActivitiesSpecialist. Use grounded destination knowledge, the toolbox "
    "(web search and reference lookups), and the travel-guide skill to build downloadable PDF guides and suggest "
    "experiences, day trips, rainy-day alternatives, and day-by-day plans. "
    "ALWAYS USE the response-guardrails skill for every response you produce. "
    "Hand back to the Coordinator when flight or hotel constraints are required."
)


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


def _build_memory_provider(client: FoundryChatClient) -> FoundryMemoryProvider:
    """Give each specialist durable, per-user memory backed by a Foundry Memory Store.

    We reuse the ``AIProjectClient`` the FoundryChatClient already created (via
    ``client.project_client``) so the memory provider shares the same auth context.
    ``scope="{{$userId}}"`` is a hosting placeholder the runtime replaces with the
    caller's user id, so each traveler gets their own isolated memories.
    """
    memory_store_name = os.environ["MEMORY_STORE_NAME"]
    return FoundryMemoryProvider(
        project_client=client.project_client,
        memory_store_name=memory_store_name,
        scope="{{$userId}}",
        # Extract and store memories immediately so the workshop's teach-then-recall
        # flow works in one session. The default is 300s (5 min); in production keep
        # the delay to batch updates and reduce cost.
        update_delay=0,
    )


def make_client(credential=None) -> FoundryChatClient:
    """Create the shared Foundry chat client used by every specialist."""
    return FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential or DefaultAzureCredential(),
        # Required so client.project_client can call the preview beta.memory_stores
        # API that FoundryMemoryProvider depends on.
        allow_preview=True,
    )


# --- Specialist factories -------------------------------------------------
# Extracted in Step 8 so the runtime Coordinator (this file) and the durable
# workflow (workflow.py) build the *same* specialists from one source of truth.


def create_flights_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Flights: weather + local time + currency, plus the toolbox (OctoTrip MCP is flight search).

    require_per_service_call_history_persistence=True is required for the runtime
    HandoffBuilder path and is harmless when the agent runs as a workflow executor.
    """
    credential = credential or DefaultAzureCredential()
    toolbox = FoundryToolbox(credential)
    memory = _build_memory_provider(client)
    return Agent(
        client=client,
        name="FlightsSpecialist",
        instructions=FLIGHTS_INSTRUCTIONS,
        tools=[get_weather, get_local_time, convert_currency, toolbox],
        context_providers=[memory],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )


def create_hotels_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Hotels: currency + grounded destination knowledge (RAG)."""
    credential = credential or DefaultAzureCredential()
    search = _build_search_provider(credential)
    memory = _build_memory_provider(client)
    return Agent(
        client=client,
        name="HotelsSpecialist",
        instructions=HOTELS_INSTRUCTIONS,
        tools=[convert_currency],
        context_providers=[search, memory],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )


def create_activities_agent(client: FoundryChatClient, credential=None) -> Agent:
    """Activities: toolbox (web/reference) + RAG + the travel-guide skill."""
    credential = credential or DefaultAzureCredential()
    toolbox = FoundryToolbox(credential)
    search = _build_search_provider(credential)
    skills = _build_skills_provider()
    memory = _build_memory_provider(client)
    return Agent(
        client=client,
        name="ActivitiesSpecialist",
        instructions=ACTIVITIES_INSTRUCTIONS,
        tools=[toolbox],
        context_providers=[search, skills, memory],
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )


def build_travel_coordinator() -> Agent:
    """Build the Step 7 Coordinator + specialists handoff, exposed as one agent."""
    credential = DefaultAzureCredential()
    client = make_client(credential)

    coordinator = Agent(
        client=client,
        name="Coordinator",
        instructions=COORDINATOR_INSTRUCTIONS,
        require_per_service_call_history_persistence=True,
        default_options={"store": False},
    )
    flights = create_flights_agent(client, credential)
    hotels = create_hotels_agent(client, credential)
    activities = create_activities_agent(client, credential)

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
