# travel_assistant/provision_memory_store.py
"""Provision the Azure AI Foundry Memory Store that backs TravelBuddy's memory.

Creates the memory store named by ``MEMORY_STORE_NAME`` if it does not already
exist. The store is configured with the user-profile capability so TravelBuddy
can remember stable traveler preferences across sessions; chat-summary is left
off to keep the workshop focused on durable facts. Safe to re-run: if the store
already exists the script leaves it alone.

Run this once, out of band, before you deploy Step 9 (venv active, ``az login``
done). It is NOT part of the hosted agent snapshot.

    python provision_memory_store.py

Required env vars (read from .env if present):

    AZURE_AI_PROJECT_ENDPOINT                     Foundry project endpoint
    AZURE_AI_MODEL_DEPLOYMENT_NAME                Chat model deployment
    AZURE_AI_EMBEDDING_MODEL_DEPLOYMENT_NAME      Embedding model deployment
    MEMORY_STORE_NAME                             Name of the memory store to create

Your identity needs ``Azure AI User`` on the Foundry project scope.
"""

import asyncio
import os

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import (
    MemoryStoreDefaultDefinition,
    MemoryStoreDefaultOptions,
)
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv(override=True)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in .env before provisioning the memory store.")
    return value


async def main() -> None:
    endpoint = required_env("AZURE_AI_PROJECT_ENDPOINT")
    memory_store_name = required_env("MEMORY_STORE_NAME")
    chat_model = required_env("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    embedding_model = required_env("AZURE_AI_EMBEDDING_MODEL_DEPLOYMENT_NAME")

    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True) as project,
    ):
        try:
            existing = await project.beta.memory_stores.get(name=memory_store_name)
            print(f"Memory store '{existing.name}' already exists (id={existing.id}); leaving as-is.")
            return
        except ResourceNotFoundError:
            pass

        print(f"Creating memory store '{memory_store_name}'...")
        definition = MemoryStoreDefaultDefinition(
            chat_model=chat_model,
            embedding_model=embedding_model,
            options=MemoryStoreDefaultOptions(
                chat_summary_enabled=False,
                user_profile_enabled=True,
                user_profile_details=(
                    "Capture durable travel preferences (home airport, cabin class, budget "
                    "band, dietary needs, favourite destinations). Avoid sensitive data such "
                    "as age, financials, precise location, and credentials."
                ),
            ),
        )
        created = await project.beta.memory_stores.create(
            name=memory_store_name,
            description="Memory store for TravelBuddy (foundry-workshop Step 9)",
            definition=definition,
        )
        print(f"Created memory store '{created.name}' (id={created.id}).")

        # Read it back to confirm the service persisted the store before the agent
        # tries to use it at runtime.
        try:
            verified = await project.beta.memory_stores.get(name=memory_store_name)
        except ResourceNotFoundError as exc:
            raise RuntimeError(
                f"Memory store '{memory_store_name}' was not found after creation; "
                "the service may not have persisted it."
            ) from exc
        print(f"Verified memory store '{verified.name}' is available (id={verified.id}).")


if __name__ == "__main__":
    asyncio.run(main())
