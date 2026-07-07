# foundry_skills/provision_skills.py
"""Upload the Foundry skill(s) to the Foundry project (out-of-band).

Mirrors ``travel_indexer/provision_index.py``: this is *tooling*, not part of the
deployed agent. ``azd ai agent init`` snapshots only ``travel_assistant/``, so this
folder is never bundled into the container. Run it once (and again after editing a
Foundry skill's ``SKILL.md``); the deployed agent downloads the result at runtime.

Each run uploads a **new version** of the skill (existing versions are left intact),
so the script is non-destructive and safe to re-run after editing a ``SKILL.md``.

Requires the **Azure AI User** (a.k.a. *Foundry User*) role on the Foundry project,
and an account with public network access (the Skills API does not support private
networking).
"""
import asyncio
import io
import os
import zipfile
from pathlib import Path

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import CreateSkillVersionFromFilesBody
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(override=True)

SKILLS_DIR = Path(__file__).parent / "skills"


def _zip_skill(skill_dir: Path) -> tuple[str, bytes]:
    """Zip a skill folder, preserving relative paths (SKILL.md sits at the root)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(skill_dir).as_posix())
    return f"{skill_dir.name}.zip", buffer.getvalue()


async def main() -> None:
    endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]

    skill_dirs = sorted(path.parent for path in SKILLS_DIR.glob("*/SKILL.md"))
    if not skill_dirs:
        raise SystemExit(f"No SKILL.md files found under {SKILLS_DIR}.")

    async with (
        DefaultAzureCredential() as cred,
        AIProjectClient(endpoint=endpoint, credential=cred, allow_preview=True) as project,
    ):
        for skill_dir in skill_dirs:
            name = skill_dir.name
            zip_name, zip_bytes = _zip_skill(skill_dir)
            version = await project.beta.skills.create_from_files(
                name, content=CreateSkillVersionFromFilesBody(files=[(zip_name, zip_bytes)])
            )
            print(f"Uploaded '{name}' (version={version.version}, skill_id={version.skill_id}).")

        listed = {skill.name async for skill in project.beta.skills.list()}
        missing = {skill_dir.name for skill_dir in skill_dirs} - listed
        if missing:
            raise SystemExit(f"Uploaded skills were not listed by Foundry: {sorted(missing)}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())