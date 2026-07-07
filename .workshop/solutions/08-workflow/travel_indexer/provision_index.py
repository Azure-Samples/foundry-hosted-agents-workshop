# travel_indexer/provision_index.py
import asyncio
import json
import os
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
)
from dotenv import load_dotenv

load_dotenv(override=True)

RECREATE = True
DATA_FILE = Path(__file__).parent / "data" / "destinations.json"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Set {name} in .env before provisioning the Search index.")
    return value


def build_index(name: str) -> SearchIndex:
    return SearchIndex(
        name=name,
        fields=[
            SimpleField(
                name="id", type=SearchFieldDataType.String, key=True, filterable=True
            ),
            SearchableField(
                name="content",
                type=SearchFieldDataType.String,
                analyzer_name="standard.lucene",
            ),
            SimpleField(
                name="city",
                type=SearchFieldDataType.String,
                filterable=True,
                retrievable=True,
            ),
            SimpleField(
                name="country",
                type=SearchFieldDataType.String,
                filterable=True,
                retrievable=True,
            ),
            SimpleField(
                name="sourceName",
                type=SearchFieldDataType.String,
                filterable=True,
                retrievable=True,
            ),
            SimpleField(
                name="sourceLink", type=SearchFieldDataType.String, retrievable=True
            ),
        ],
    )


def load_destinations() -> list[dict[str, str]]:
    documents = []
    for record in json.loads(DATA_FILE.read_text(encoding="utf-8")):
        highlights = "; ".join(record["highlights"])
        documents.append(
            {
                "id": record["id"],
                "city": record["city"],
                "country": record["country"],
                "content": (
                    f"City: {record['city']}. Country: {record['country']}. "
                    f"Summary: {record['summary']} Highlights: {highlights}."
                ),
                "sourceName": f"TravelBuddy destinations: {record['city']}",
                "sourceLink": f"travelbuddy://destinations/{record['id']}",
            }
        )
    return documents


async def main() -> None:
    endpoint = required_env("AZURE_AI_SEARCH_ENDPOINT")
    index_name = required_env("AZURE_AI_SEARCH_INDEX_NAME")
    documents = load_destinations()

    async with (
        DefaultAzureCredential() as credential,
        SearchIndexClient(endpoint=endpoint, credential=credential) as index_client,
        SearchClient(
            endpoint=endpoint, index_name=index_name, credential=credential
        ) as search_client,
    ):
        try:
            await index_client.get_index(index_name)
            if RECREATE:
                print(f"Deleting existing index '{index_name}'...")
                await index_client.delete_index(index_name)
                print(f"Creating index '{index_name}'...")
                await index_client.create_index(build_index(index_name))
            else:
                print(f"Index '{index_name}' already exists; keeping existing schema.")
        except ResourceNotFoundError:
            print(f"Creating index '{index_name}'...")
            await index_client.create_index(build_index(index_name))

        print(f"Uploading {len(documents)} destination document(s)...")
        results = await search_client.merge_or_upload_documents(documents=documents)
        failed = [
            (result.key, result.error_message)
            for result in results
            if not result.succeeded
        ]
        if failed:
            raise RuntimeError(f"Failed to upload documents: {failed}")

    print("Done. TravelBuddy's destination index is ready.")


if __name__ == "__main__":
    asyncio.run(main())
