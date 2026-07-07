# travel_assistant/main.py
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
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.ai.projects.aio import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from dotenv import load_dotenv

from tools import convert_currency, get_local_time, get_weather

load_dotenv(override=True)

logger = logging.getLogger(__name__)


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


def main() -> None:
    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    # Carried from Step 4: local function tools + the Foundry Toolbox (web search,
    # Code Interpreter, OctoTrip flights MCP).
    toolbox = FoundryToolbox(credential)
    tools = [
        get_weather,
        get_local_time,
        convert_currency,
        toolbox,
    ]

    context_providers = []

    # Carried from Step 5: RAG grounding over the destinations index.
    search_endpoint = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
    search_index_name = os.environ["AZURE_AI_SEARCH_INDEX_NAME"]
    context_providers.append(
        AzureAISearchContextProvider(
            source_id="travelbuddy_destinations",
            endpoint=search_endpoint,
            index_name=search_index_name,
            credential=credential,
            mode="semantic",
            top_k=3,
        )
    )

    # NEW in Step 6: skills. The LOCAL travel-guide skill ships in skills/; the required
    # Foundry skill is downloaded from the project at startup. One provider
    # serves both (see _build_skills_provider above).
    context_providers.append(_build_skills_provider())

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
            "index does not contain enough detail, say what is missing. "
            "When the traveler wants a downloadable trip guide or a day-by-day plan, "
            "use the travel-guide skill to render a grounded PDF guide before answering. "
            "ALWAYS USE the response-guardrails skill for every response you produce."
        ),
        tools=tools,
        context_providers=context_providers,
        default_options={"store": False},
    )

    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
