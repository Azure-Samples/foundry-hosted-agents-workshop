#!/usr/bin/env python3
"""Best-effort cleanup for Foundry Agent Framework workshop resources."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_PREFIX = "foundry-workshop"
WORKSHOP_TAG_KEY = "workshop"
WORKSHOP_TAG_VALUE = "foundry-agent-framework"
PROJECT_SCOPES = (
    "https://ai.azure.com/.default",
    "https://cognitiveservices.azure.com/.default",
)
SEARCH_SCOPES = ("https://search.azure.com/.default",)
SEARCH_API_VERSION = "2024-07-01"
PROJECT_API_VERSIONS = ("2025-05-01-preview", "2025-05-01", "2024-05-01-preview")
VALID_KINDS = ("agents", "search", "memory", "files", "skills", "all")


@dataclass
class DeleteOutcome:
    status: str
    message: str = ""


@dataclass
class Candidate:
    name: str
    details: str = ""
    should_delete: bool = False
    skip_reason: str = "no prefix match"
    delete: Callable[[], DeleteOutcome] | None = None


@dataclass
class Section:
    title: str
    candidates: list[Candidate] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


@dataclass
class Counts:
    deleted: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: "Counts") -> None:
        self.deleted += other.deleted
        self.failed += other.failed
        self.skipped += other.skipped


def load_env() -> None:
    """Load .env without requiring python-dotenv."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            return
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value
    else:
        load_dotenv(override=True)


def has_prefix(name: str | None, prefix: str) -> bool:
    if not name:
        return False
    return name == prefix or name.startswith(f"{prefix}-")


def trim_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return " ".join(text.split())[:300]


def is_not_found(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status == 404:
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return "not found" in text or "resourcenotfound" in text or "404" in text


def get_credential() -> tuple[Any | None, str | None]:
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
    except ImportError:
        return None, "install requirements first (missing package: azure-identity)"
    try:
        return DefaultAzureCredential(), None
    except Exception as exc:  # pragma: no cover - constructor should rarely fail
        return None, f"couldn't initialize DefaultAzureCredential: {trim_error(exc)}"


class RestError(Exception):
    def __init__(self, status_code: int | None, message: str):
        super().__init__(message)
        self.status_code = status_code


class RestClient:
    def __init__(self, endpoint: str, credential: Any, scopes: Iterable[str]):
        self.endpoint = endpoint.rstrip("/")
        self.credential = credential
        self.scopes = tuple(scopes)

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def delete(self, path: str) -> DeleteOutcome:
        try:
            self._request("DELETE", path)
            return DeleteOutcome("deleted")
        except RestError as exc:
            if exc.status_code == 404:
                return DeleteOutcome("skipped", "not found")
            return DeleteOutcome("failed", str(exc))

    def _request(self, method: str, path: str) -> Any:
        url = f"{self.endpoint}{path}"
        last_error: RestError | None = None
        for scope in self.scopes:
            try:
                token = self.credential.get_token(scope).token
                request = Request(url, method=method, headers={"Authorization": f"Bearer {token}"})
                if method == "DELETE":
                    request.add_header("Content-Length", "0")
                with urlopen(request, timeout=30) as response:  # nosec - URL comes from user env
                    body = response.read().decode("utf-8")
                    if not body:
                        return None
                    return json.loads(body)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                message = body or exc.reason or f"HTTP {exc.code}"
                last_error = RestError(exc.code, message)
                if exc.code not in (401, 403):
                    break
            except URLError as exc:
                raise RestError(None, trim_error(exc)) from exc
            except Exception as exc:
                raise RestError(None, trim_error(exc)) from exc
        assert last_error is not None
        raise last_error


def try_project_client(endpoint: str, credential: Any) -> tuple[Any | None, str | None]:
    try:
        from azure.ai.projects import AIProjectClient  # type: ignore
    except ImportError:
        return None, "azure-ai-projects is not installed; using REST fallback"
    try:
        return AIProjectClient(endpoint=endpoint, credential=credential), None
    except Exception as exc:
        return None, f"couldn't create azure-ai-projects client: {trim_error(exc)}"


def as_list(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, tuple):
        return list(result)
    if isinstance(result, dict):
        return extract_items(result)
    try:
        return list(result)
    except TypeError:
        return [result]


def extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("value", "data", "items", "agents", "files", "skills", "memoryStores"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def attr(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return None


def item_id(item: Any) -> str:
    value = attr(item, "id", "agent_id", "assistant_id", "file_id", "skill_id", "name")
    return str(value) if value is not None else ""


def item_name(item: Any, fallback: str = "") -> str:
    value = attr(item, "name", "filename", "display_name", "id")
    return str(value) if value is not None else fallback


def item_tags(item: Any) -> dict[str, Any]:
    tags = attr(item, "tags", "metadata", "properties")
    if isinstance(tags, dict):
        return tags
    if isinstance(tags, list):
        result: dict[str, Any] = {}
        for entry in tags:
            if isinstance(entry, dict):
                key = entry.get("key") or entry.get("name")
                value = entry.get("value")
                if key:
                    result[str(key)] = value
            elif isinstance(entry, str):
                result[entry] = True
        return result
    return {}


def has_workshop_tag(item: Any) -> bool:
    tags = item_tags(item)
    return str(tags.get(WORKSHOP_TAG_KEY, "")).lower() == WORKSHOP_TAG_VALUE


def format_details(identifier: str = "", label: str = "id") -> str:
    return f"[{label}={identifier}]" if identifier else ""


def sdk_list(client_candidates: Iterable[Any], method_names: Iterable[str]) -> tuple[list[Any] | None, str | None, Any | None]:
    errors: list[str] = []
    for client in client_candidates:
        if client is None:
            continue
        for method_name in method_names:
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                return as_list(method()), None, client
            except TypeError:
                continue
            except Exception as exc:
                errors.append(f"{method_name}: {trim_error(exc)}")
    return None, "; ".join(errors) if errors else None, None


def sdk_delete(target: Any, identifier: str, method_names: Iterable[str], kw_names: Iterable[str]) -> DeleteOutcome:
    errors: list[str] = []
    for method_name in method_names:
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [((identifier,), {})]
        attempts.extend(((), {kw_name: identifier}) for kw_name in kw_names)
        for args, kwargs in attempts:
            try:
                method(*args, **kwargs)
                return DeleteOutcome("deleted")
            except TypeError:
                continue
            except Exception as exc:
                if is_not_found(exc):
                    return DeleteOutcome("skipped", "not found")
                errors.append(trim_error(exc))
                break
    return DeleteOutcome("failed", "; ".join(errors) or "no supported SDK delete method")


def rest_collection(rest: RestClient, paths: Iterable[str]) -> tuple[list[Any] | None, str | None, str | None]:
    errors: list[str] = []
    for path in paths:
        try:
            payload = rest.get(path)
            return extract_items(payload), None, path
        except RestError as exc:
            if exc.status_code == 404:
                continue
            errors.append(f"{path}: {trim_error(exc)}")
    return None, "; ".join(errors) if errors else "no supported REST endpoint found", None


def child_delete_path(list_path: str, identifier: str) -> str:
    root, _, query = list_path.partition("?")
    suffix = f"/{quote(identifier, safe='')}"
    return f"{root.rstrip('/')}{suffix}?{query}" if query else f"{root.rstrip('/')}{suffix}"


def project_context(section: Section) -> tuple[str | None, Any | None, Any | None, RestClient | None]:
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        section.messages.append("couldn't list — set AZURE_AI_PROJECT_ENDPOINT")
        return None, None, None, None
    credential, cred_error = get_credential()
    if cred_error:
        section.messages.append(f"couldn't list — {cred_error}")
        return endpoint, None, None, None
    client, client_note = try_project_client(endpoint, credential)
    if client_note:
        section.messages.append(client_note)
    return endpoint, credential, client, RestClient(endpoint, credential, PROJECT_SCOPES)


def project_paths(kind: str) -> list[str]:
    paths: list[str] = []
    for api_version in PROJECT_API_VERSIONS:
        if kind == "agents":
            paths.extend(
                [
                    f"/assistants?api-version={api_version}",
                    f"/agents?api-version={api_version}",
                    f"/openai/assistants?api-version={api_version}",
                ]
            )
        elif kind == "files":
            paths.extend([f"/files?api-version={api_version}", f"/openai/files?api-version={api_version}"])
        elif kind == "skills":
            paths.extend([f"/skills?api-version={api_version}"])
    return paths


def collect_agents(prefix: str) -> Section:
    section = Section("Hosted agents")
    _, _, client, rest = project_context(section)
    if client is None and rest is None:
        return section

    sdk_target = None
    items = None
    if client is not None:
        agents_client = getattr(client, "agents", None)
        items, error, sdk_target = sdk_list((agents_client, client), ("list_agents", "list", "list_assistants"))
        if error:
            section.messages.append(f"SDK list failed: {error}")

    list_path = None
    if items is None and rest is not None:
        items, error, list_path = rest_collection(rest, project_paths("agents"))
        if error:
            section.messages.append(f"couldn't list via REST — {error}")
            return section

    for item in items or []:
        identifier = item_id(item)
        name = item_name(item, identifier)
        prefix_match = has_prefix(name, prefix)
        tag_match = has_workshop_tag(item)
        if prefix_match:
            reason = ""
            should_delete = True
        elif tag_match:
            reason = "workshop tag match but no prefix match; safety contract"
            should_delete = False
        else:
            reason = "no prefix match"
            should_delete = False

        delete = None
        if should_delete and identifier:
            if sdk_target is not None:
                delete = lambda target=sdk_target, rid=identifier: sdk_delete(
                    target,
                    rid,
                    ("delete_agent", "delete_assistant", "delete"),
                    ("agent_id", "assistant_id", "id"),
                )
            elif rest is not None and list_path:
                delete_path = child_delete_path(list_path, identifier)
                delete = lambda r=rest, path=delete_path: r.delete(path)
        section.candidates.append(
            Candidate(
                name=name,
                details=format_details(identifier, "agent_id"),
                should_delete=should_delete,
                skip_reason=reason,
                delete=delete,
            )
        )
    return section


def collect_search(prefix: str) -> Section:
    section = Section("Azure AI Search index")
    index_name = os.getenv("AZURE_AI_SEARCH_INDEX_NAME", "").strip() or f"{prefix}-destinations"
    if not has_prefix(index_name, prefix):
        section.candidates.append(Candidate(name=index_name, skip_reason="index name has no prefix match"))
        return section

    endpoint = os.getenv("AZURE_AI_SEARCH_ENDPOINT", "").strip()
    if not endpoint:
        section.messages.append("couldn't check — set AZURE_AI_SEARCH_ENDPOINT")
        section.candidates.append(Candidate(name=index_name, skip_reason="missing AZURE_AI_SEARCH_ENDPOINT"))
        return section

    credential, cred_error = get_credential()
    if cred_error:
        section.messages.append(f"couldn't check — {cred_error}")
        return section
    rest = RestClient(endpoint, credential, SEARCH_SCOPES)
    path = f"/indexes/{quote(index_name, safe='')}?api-version={SEARCH_API_VERSION}"
    try:
        rest.get(path)
    except RestError as exc:
        if exc.status_code == 404:
            section.candidates.append(Candidate(name=index_name, skip_reason="not found"))
        else:
            section.messages.append(f"couldn't check — {trim_error(exc)}")
        return section

    section.candidates.append(Candidate(name=index_name, should_delete=True, delete=lambda r=rest, p=path: r.delete(p)))
    return section


def collect_memory(prefix: str) -> Section:
    section = Section("Memory store")
    memory_store_id = os.getenv("MEMORY_STORE_NAME", "").strip()
    if not memory_store_id:
        section.messages.append("No MEMORY_STORE_NAME set.")
        return section
    if not has_prefix(memory_store_id, prefix):
        section.candidates.append(Candidate(name=memory_store_id, skip_reason="MEMORY_STORE_NAME has no prefix match"))
        return section

    _, _, client, rest = project_context(section)
    if client is None and rest is None:
        section.candidates.append(Candidate(name=memory_store_id, skip_reason="couldn't prepare project client"))
        return section

    delete: Callable[[], DeleteOutcome] | None = None
    if client is not None:
        targets = (
            getattr(client, "memory_stores", None),
            getattr(client, "memory", None),
            getattr(getattr(client, "agents", None), "memory_stores", None),
        )
        for target in targets:
            if target is not None:
                delete = lambda target=target, rid=memory_store_id: sdk_delete(
                    target,
                    rid,
                    ("delete_memory_store", "delete_store", "delete"),
                    ("memory_store_id", "store_id", "id", "name"),
                )
                break
    if delete is None and rest is not None:
        paths = [
            f"/memoryStores/{quote(memory_store_id, safe='')}?api-version=2025-05-01-preview",
            f"/memory/stores/{quote(memory_store_id, safe='')}?api-version=2025-05-01-preview",
        ]

        def rest_delete(paths: list[str] = paths, rest: RestClient = rest) -> DeleteOutcome:
            last = DeleteOutcome("failed", "no supported REST endpoint found")
            for path in paths:
                outcome = rest.delete(path)
                if outcome.status in ("deleted", "skipped"):
                    return outcome
                last = outcome
            return last

        delete = rest_delete

    section.candidates.append(
        Candidate(
            name=memory_store_id,
            details=format_details(memory_store_id, "memory_store_id"),
            should_delete=True,
            delete=delete,
        )
    )
    return section


def collect_files(prefix: str) -> Section:
    section = Section("Project files")
    _, _, client, rest = project_context(section)
    if client is None and rest is None:
        return section

    sdk_target = None
    items = None
    if client is not None:
        agents_client = getattr(client, "agents", None)
        files_clients = (getattr(agents_client, "files", None), getattr(client, "files", None), agents_client)
        items, error, sdk_target = sdk_list(files_clients, ("list_files", "list", "files"))
        if error:
            section.messages.append(f"SDK list failed: {error}")

    list_path = None
    if items is None and rest is not None:
        items, error, list_path = rest_collection(rest, project_paths("files"))
        if error:
            section.messages.append(f"couldn't list via REST — {error}")
            return section

    for item in items or []:
        identifier = item_id(item)
        name = item_name(item, identifier)
        should_delete = has_prefix(name, prefix)
        delete = None
        if should_delete and identifier:
            if sdk_target is not None:
                delete = lambda target=sdk_target, rid=identifier: sdk_delete(
                    target,
                    rid,
                    ("delete_file", "delete"),
                    ("file_id", "id"),
                )
            elif rest is not None and list_path:
                delete_path = child_delete_path(list_path, identifier)
                delete = lambda r=rest, path=delete_path: r.delete(path)
        section.candidates.append(
            Candidate(
                name=name,
                details=format_details(identifier, "file_id"),
                should_delete=should_delete,
                skip_reason="no prefix match",
                delete=delete,
            )
        )
    return section


def collect_skills(prefix: str) -> Section:
    section = Section("Skills")
    _, _, client, rest = project_context(section)
    if client is None and rest is None:
        return section

    sdk_target = None
    items = None
    if client is not None:
        agents_client = getattr(client, "agents", None)
        skill_clients = (getattr(client, "skills", None), getattr(agents_client, "skills", None), client)
        items, error, sdk_target = sdk_list(skill_clients, ("list_skills", "list", "skills"))
        if error:
            section.messages.append(f"SDK list failed: {error}")

    list_path = None
    if items is None and rest is not None:
        items, error, list_path = rest_collection(rest, project_paths("skills"))
        if error:
            section.messages.append(f"couldn't list via REST — {error}")
            return section

    for item in items or []:
        identifier = item_id(item)
        name = item_name(item, identifier)
        should_delete = has_prefix(name, prefix)
        delete = None
        if should_delete:
            delete_id = identifier or name
            if sdk_target is not None:
                delete = lambda target=sdk_target, rid=delete_id: sdk_delete(
                    target,
                    rid,
                    ("delete_skill", "delete"),
                    ("skill_id", "id", "name"),
                )
            elif rest is not None and list_path:
                delete_path = child_delete_path(list_path, delete_id)
                delete = lambda r=rest, path=delete_path: r.delete(path)
        section.candidates.append(
            Candidate(
                name=name,
                details=format_details(identifier, "skill_id"),
                should_delete=should_delete,
                skip_reason="no prefix match",
                delete=delete,
            )
        )
    return section


def selected_kinds(only: str) -> list[str]:
    only = only.lower().strip()
    if only == "all":
        return ["agents", "search", "memory", "files", "skills"]
    if only not in VALID_KINDS:
        print(f"Warning: unknown resource kind '{only}' — no-op. Valid kinds: {', '.join(VALID_KINDS)}.")
        return []
    return [only]


def collect_sections(kinds: Iterable[str], prefix: str) -> list[Section]:
    collectors: dict[str, Callable[[str], Section]] = {
        "agents": collect_agents,
        "search": collect_search,
        "memory": collect_memory,
        "files": collect_files,
        "skills": collect_skills,
    }
    return [collectors[kind](prefix) for kind in kinds]


def planned_delete_count(sections: Iterable[Section]) -> int:
    return sum(1 for section in sections for candidate in section.candidates if candidate.should_delete)


def print_section(section: Section, apply: bool) -> Counts:
    print(f"\n== {section.title} ==")
    for message in section.messages:
        print(f"! {message}")

    counts = Counts()
    if not section.candidates:
        print("No resources found.")
        return counts

    pending = 0
    for candidate in section.candidates:
        detail = f" {candidate.details}" if candidate.details else ""
        if not candidate.should_delete:
            print(f"- {candidate.name}{detail} → skipped ({candidate.skip_reason})")
            counts.skipped += 1
            continue

        if not apply:
            print(f"- {candidate.name}{detail} → would delete")
            pending += 1
            continue

        if candidate.delete is None:
            outcome = DeleteOutcome("failed", "no supported delete method")
        else:
            outcome = candidate.delete()
        if outcome.status == "deleted":
            print(f"- {candidate.name}{detail} → deleted ✅")
            counts.deleted += 1
        elif outcome.status == "skipped":
            reason = f": {outcome.message}" if outcome.message else ""
            print(f"- {candidate.name}{detail} → skipped{reason}")
            counts.skipped += 1
        else:
            reason = f": {outcome.message}" if outcome.message else ""
            print(f"- {candidate.name}{detail} → failed ❌{reason}")
            counts.failed += 1

    if apply:
        print(f"{counts.deleted} deleted, {counts.failed} failed, {counts.skipped} skipped.")
    else:
        print(f"{pending} to delete, {counts.skipped} skipped.")
    return counts


def confirm_or_abort(count: int) -> bool:
    if count == 0:
        return True
    try:
        answer = input(f"About to delete {count} resources. Continue? [y/N] ")
    except EOFError:
        print("No confirmation received; aborting.")
        return False
    return answer.strip().lower() in ("y", "yes")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or delete workshop-created Azure resources in a Foundry project."
    )
    parser.add_argument("--apply", action="store_true", help="actually delete matching resources")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompt when used with --apply")
    parser.add_argument(
        "--only",
        default="all",
        metavar="KIND",
        help="resource kind to clean: agents, search, memory, files, skills, or all (default: all)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = parse_args(argv or sys.argv[1:])
    prefix = os.getenv("WORKSHOP_RESOURCE_PREFIX", DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
    kinds = selected_kinds(args.only)

    print(f"Cleanup mode: {'apply' if args.apply else 'dry-run'}")
    print(f"Resource prefix: {prefix}")

    sections = collect_sections(kinds, prefix)
    delete_count = planned_delete_count(sections)

    if args.apply and not args.yes and not confirm_or_abort(delete_count):
        print("Cleanup aborted.")
        return 1

    totals = Counts()
    for section in sections:
        totals.add(print_section(section, args.apply))

    if not args.apply:
        print(f"\nCleanup: 0 deleted, 0 failed, {totals.skipped} skipped. Dry-run: {delete_count} would be deleted.")
        return 0

    print(f"\nCleanup: {totals.deleted} deleted, {totals.failed} failed, {totals.skipped} skipped.")
    return 1 if totals.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
