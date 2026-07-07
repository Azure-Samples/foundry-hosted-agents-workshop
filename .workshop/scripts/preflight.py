#!/usr/bin/env python3
"""Preflight checks for the Foundry Agent Framework workshop.

Usage:
    python .workshop/scripts/preflight.py
    python .workshop/scripts/preflight.py --step 5
    python .workshop/scripts/preflight.py --json
    python .workshop/scripts/preflight.py --verbose

The script validates the local Python, Azure CLI, Azure login, `.env`, Foundry
project endpoint, model deployment, and any step-specific prerequisites. It is
safe to re-run before later workshop steps.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

PASS = "passed"
WARN = "warning"
FAIL = "failed"

AI_SCOPE = "https://ai.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.com/.default"
PROJECT_API_VERSION = "v1"
SEARCH_API_VERSION = "2024-07-01"
REQUIREMENTS_HINT = "pip install -r travel_assistant/requirements.txt"
MINIMUM_ENV_VARS = (
    "AZURE_AI_PROJECT_ENDPOINT",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "WORKSHOP_RESOURCE_PREFIX",
)
STEP3_ENV_VARS = ("MCP_SERVER_LABEL", "MCP_SERVER_URL")
STEP5_ENV_VARS = ("AZURE_AI_SEARCH_ENDPOINT", "AZURE_AI_SEARCH_INDEX_NAME")


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    remediation: str = ""
    raw_error: str = ""

    def to_json(self, verbose: bool = False) -> dict[str, str]:
        payload = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "remediation": self.remediation,
        }
        if verbose and self.raw_error:
            payload["raw_error"] = self.raw_error
        return payload


class EnvConfig:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.env_path = repo_root / ".env"
        self.file_values: dict[str, str] = {}
        self.read_error = ""
        if self.env_path.exists():
            try:
                self.file_values = parse_env_file(self.env_path)
            except Exception as exc:  # defensive by design
                self.read_error = str(exc)

    def get(self, key: str) -> str:
        return os.environ.get(key, self.file_values.get(key, "")).strip()

    def missing_from_file(self, keys: Iterable[str]) -> list[str]:
        return [key for key in keys if not self.file_values.get(key, "").strip()]

    def missing_from_config(self, keys: Iterable[str]) -> list[str]:
        return [key for key in keys if not self.get(key)]


class Colors:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def color(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def green(self, text: str) -> str:
        return self.color(text, "32")

    def yellow(self, text: str) -> str:
        return self.color(text, "33")

    def red(self, text: str) -> str:
        return self.color(text, "31")


def repo_root() -> Path:
    # preflight.py lives at .workshop/scripts/, so the repo root is two levels up.
    return Path(__file__).resolve().parents[2]


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        values[key] = normalize_env_value(value)
    return values


def normalize_env_value(value: str) -> str:
    value = strip_unquoted_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace(r"\n", "\n").replace(r"\r", "\r")
    return value.strip()


def strip_unquoted_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value


def run_command(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        check=False,
    )


def raw_exception() -> str:
    return traceback.format_exc().strip()


def check_python_version(_: EnvConfig) -> CheckResult:
    version = sys.version_info
    version_text = platform.python_version()
    if version >= (3, 10):
        return CheckResult("Python", PASS, f"Python {version_text}")
    return CheckResult(
        "Python",
        FAIL,
        f"Python {version_text}",
        "Install Python 3.10 or newer, then re-run this script.",
    )


def check_az_cli(_: EnvConfig) -> CheckResult:
    try:
        result = run_command(["az", "--version"], timeout=20)
    except FileNotFoundError:
        return CheckResult(
            "Azure CLI",
            FAIL,
            "Azure CLI not found",
            "Install Azure CLI: https://learn.microsoft.com/cli/azure/install-azure-cli",
        )
    except Exception:
        return CheckResult(
            "Azure CLI",
            FAIL,
            "Azure CLI check failed",
            "Install or repair Azure CLI, then re-run this script.",
            raw_exception(),
        )

    if result.returncode != 0:
        return CheckResult(
            "Azure CLI",
            FAIL,
            "az --version failed",
            "Install or repair Azure CLI, then re-run this script.",
            (result.stderr or result.stdout).strip(),
        )

    output = result.stdout or result.stderr
    match = re.search(r"azure-cli\s+([^\s*]+)", output)
    version = match.group(1) if match else "installed"
    return CheckResult("Azure CLI", PASS, f"Azure CLI {version}")


def check_az_login(_: EnvConfig) -> CheckResult:
    try:
        result = run_command(["az", "account", "show", "--output", "json"], timeout=30)
    except FileNotFoundError:
        return CheckResult(
            "az login",
            FAIL,
            "Azure CLI not found",
            "Install Azure CLI, run `az login`, then re-run this script.",
        )
    except Exception:
        return CheckResult(
            "az login",
            FAIL,
            "Unable to check Azure login",
            "Run `az login` then re-run this script.",
            raw_exception(),
        )

    if result.returncode != 0:
        return CheckResult(
            "az login",
            FAIL,
            "Not signed in to Azure CLI",
            "Run `az login` then re-run this script.",
            (result.stderr or result.stdout).strip(),
        )

    try:
        account = json.loads(result.stdout)
    except json.JSONDecodeError:
        return CheckResult(
            "az login",
            FAIL,
            "Azure CLI returned invalid account JSON",
            "Run `az login` again or update Azure CLI, then re-run this script.",
            result.stdout,
        )

    tenant_id = account.get("tenantId") or account.get("tenantDefaultDomain")
    if not tenant_id:
        return CheckResult(
            "az login",
            FAIL,
            "Azure account has no active tenant",
            "Run `az login --tenant <tenant-id>` for the tenant that owns your Foundry project.",
            result.stdout,
        )

    subscription = account.get("name") or account.get("id") or "active subscription"
    return CheckResult("az login", PASS, f"az login active for {subscription}")


def check_env_file(config: EnvConfig) -> CheckResult:
    if not config.env_path.exists():
        return CheckResult(
            ".env",
            FAIL,
            ".env file not found at repo root",
            "Copy `.env.example` to `.env` or create `.env`, then set AZURE_AI_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME, and WORKSHOP_RESOURCE_PREFIX.",
        )
    if config.read_error:
        return CheckResult(
            ".env",
            FAIL,
            ".env could not be read",
            "Fix the .env file encoding or syntax, then re-run this script.",
            config.read_error,
        )
    missing = config.missing_from_file(MINIMUM_ENV_VARS)
    if missing:
        return CheckResult(
            ".env",
            FAIL,
            f".env is missing {', '.join(missing)}",
            "Add the missing step 0/1 variables to `.env`, then re-run this script.",
        )
    return CheckResult(".env", PASS, ".env contains step 0/1 variables")


def import_default_credential() -> tuple[Any | None, str]:
    """Return ``(DefaultAzureCredential, "")`` or ``(None, raw_error)``.

    The result is cached so the four checks that need a credential only pay
    the import cost — and only surface the misleading "not installed" message
    — once per preflight run.
    """
    cached = getattr(import_default_credential, "_cached", None)
    if cached is not None:
        return cached
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore

        result: tuple[Any | None, str] = (DefaultAzureCredential, "")
    except ImportError:
        # ModuleNotFoundError is a subclass of ImportError; both mean the
        # package isn't importable in this interpreter.
        result = (None, raw_exception())
    except Exception:
        # Something else broke during import (e.g. partially installed
        # package, version conflict, broken native dependency). Preserve the
        # trace so --verbose can surface it.
        result = (None, raw_exception())
    import_default_credential._cached = result  # type: ignore[attr-defined]
    return result


def reset_import_default_credential_cache() -> None:
    """Clear the cached import result. Intended for tests."""
    if hasattr(import_default_credential, "_cached"):
        delattr(import_default_credential, "_cached")


def azure_identity_import_failed(raw_error: str) -> bool:
    """Return True when ``raw_error`` looks like a missing-package failure."""
    if not raw_error:
        return False
    head = raw_error.strip().splitlines()[-1] if raw_error.strip() else ""
    return head.startswith(("ModuleNotFoundError", "ImportError"))


def azure_identity_warning(context: str = "") -> tuple[str, str]:
    """Build the user-facing message + remediation for a missing/broken
    azure-identity install.

    ``context`` is appended to the base message (e.g. "; skipping model
    deployment lookup") so each check keeps its existing wording.
    """
    interpreter = sys.executable or "the active Python interpreter"
    base = f"azure-identity not importable in active interpreter ({interpreter})"
    if context:
        message = f"{base}{context}"
    else:
        message = base
    remediation = (
        f"Install requirements in the same interpreter: "
        f'`"{interpreter}" -m pip install -r travel_assistant/requirements.txt`'
    )
    return message, remediation


def build_credential(default_credential: Any) -> Any:
    return default_credential(exclude_interactive_browser_credential=True)


def get_token(default_credential: Any, scope: str) -> tuple[str | None, str]:
    try:
        credential = build_credential(default_credential)
        token = credential.get_token(scope)
        close = getattr(credential, "close", None)
        if callable(close):
            close()
        return token.token, ""
    except Exception:
        return None, raw_exception()


def validate_url(url: str) -> tuple[urllib.parse.ParseResult | None, str]:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return None, str(exc)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "Use a full https:// endpoint URL."
    return parsed, ""


def check_host_resolves(parsed: urllib.parse.ParseResult) -> tuple[bool, str]:
    host = parsed.hostname
    if not host:
        return False, "Endpoint URL has no host."
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return True, ""
    except Exception:
        return False, raw_exception()


def authenticated_get(url: str, token: str, timeout: int = 15) -> tuple[int | None, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "foundry-workshop-preflight/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2048).decode("utf-8", errors="replace")
            return response.getcode(), body, ""
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode("utf-8", errors="replace")
        return exc.code, body, f"HTTP {exc.code}: {body}"
    except Exception:
        return None, "", raw_exception()


def project_probe_url(endpoint: str) -> str:
    # The bare project root ("/api/projects/<name>") has no GET operation and
    # always returns 404, so probe a real data-plane sub-resource instead. The
    # deployments list is the natural choice: it returns 200 for a valid,
    # authorized project even when no models are deployed yet.
    parsed = urllib.parse.urlsplit(endpoint)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "api-version" for key, _value in query):
        query.append(("api-version", PROJECT_API_VERSION))
    path = f"{parsed.path.rstrip('/')}/deployments"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, urllib.parse.urlencode(query), parsed.fragment)
    )


def check_project_endpoint(config: EnvConfig) -> CheckResult:
    endpoint = config.get("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            "AZURE_AI_PROJECT_ENDPOINT is not set",
            "Set AZURE_AI_PROJECT_ENDPOINT in `.env` to your Azure AI Foundry project endpoint.",
        )

    parsed, url_error = validate_url(endpoint)
    if not parsed:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            "AZURE_AI_PROJECT_ENDPOINT is not a valid URL",
            f"Fix AZURE_AI_PROJECT_ENDPOINT in `.env`. {url_error}",
        )

    resolves, dns_error = check_host_resolves(parsed)
    if not resolves:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            f"Project host {parsed.hostname} does not resolve",
            "Check the endpoint value, VPN/network access, and DNS, then re-run this script.",
            dns_error,
        )

    default_credential, import_error = import_default_credential()
    if default_credential is None:
        message, remediation = azure_identity_warning()
        return CheckResult(
            "Foundry project endpoint",
            WARN,
            message,
            remediation,
            import_error,
        )

    token, token_error = get_token(default_credential, AI_SCOPE)
    if not token:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            "Could not get an Azure AI token with DefaultAzureCredential",
            "Run `az login`, ensure your account can access the Foundry project, then re-run this script.",
            token_error,
        )

    status, _body, get_error = authenticated_get(project_probe_url(endpoint), token)
    if status is None:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            "Project endpoint is not reachable",
            "Check AZURE_AI_PROJECT_ENDPOINT, network access, and Azure service health.",
            get_error,
        )
    if status in {401, 403}:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            f"Project endpoint returned HTTP {status}",
            "Grant your signed-in account access to the Foundry project, then re-run this script.",
            get_error,
        )
    if status == 404:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            "Project endpoint returned HTTP 404",
            "Confirm AZURE_AI_PROJECT_ENDPOINT points to the project, not the hub or portal page.",
            get_error,
        )
    if status >= 500:
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            f"Project endpoint returned HTTP {status}",
            "Retry later or check Azure service health for Azure AI Foundry.",
            get_error,
        )
    if not 200 <= status < 300:
        # Any other status (e.g. 400 from a bad api-version, 405, 429 throttling)
        # is not a successful probe and must not be reported as reachable.
        return CheckResult(
            "Foundry project endpoint",
            FAIL,
            f"Project endpoint returned HTTP {status}",
            "Confirm AZURE_AI_PROJECT_ENDPOINT and its api-version are correct, then re-run this script.",
            get_error,
        )
    return CheckResult("Foundry project endpoint", PASS, f"Foundry project endpoint reachable (HTTP {status})")


def extract_deployment_name(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("name", "deploymentName", "deployment_name", "modelDeploymentName", "model_deployment_name"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    for attr in ("name", "deployment_name", "deploymentName", "model_deployment_name", "modelDeploymentName"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def list_deployments_with_projects_sdk(endpoint: str, default_credential: Any) -> tuple[list[str] | None, str]:
    try:
        from azure.ai.projects import AIProjectClient  # type: ignore
    except Exception as exc:
        return None, f"azure-ai-projects not installed: {exc}"

    client = None
    try:
        credential = build_credential(default_credential)
        try:
            client = AIProjectClient(endpoint=endpoint, credential=credential)
        except TypeError:
            client = AIProjectClient(endpoint, credential)

        deployments_client = getattr(client, "deployments", None) or getattr(client, "deployments_client", None)
        if deployments_client is None:
            return None, "Installed azure-ai-projects SDK does not expose a deployments client."

        list_method = getattr(deployments_client, "list", None) or getattr(deployments_client, "list_deployments", None)
        if not callable(list_method):
            return None, "Installed azure-ai-projects SDK does not expose a deployment list method."

        deployments = list(list_method())
        names = sorted({name for name in (extract_deployment_name(item) for item in deployments) if name})
        return names, ""
    except Exception:
        return None, raw_exception()
    finally:
        for obj in (client, locals().get("credential")):
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def agent_framework_installed() -> bool:
    try:
        __import__("agent_framework")
        return True
    except Exception:
        return False


def check_model_deployment(config: EnvConfig) -> CheckResult:
    endpoint = config.get("AZURE_AI_PROJECT_ENDPOINT")
    model_name = config.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    if not model_name:
        return CheckResult(
            "Model deployment",
            FAIL,
            "AZURE_AI_MODEL_DEPLOYMENT_NAME is not set",
            "Set AZURE_AI_MODEL_DEPLOYMENT_NAME in `.env` to the model deployment used by the workshop.",
        )
    if not endpoint:
        return CheckResult(
            "Model deployment",
            WARN,
            "Cannot verify deployment without AZURE_AI_PROJECT_ENDPOINT",
            "Set AZURE_AI_PROJECT_ENDPOINT in `.env`, then re-run this script.",
        )

    default_credential, import_error = import_default_credential()
    if default_credential is None:
        message, remediation = azure_identity_warning("; skipping model deployment lookup")
        return CheckResult(
            "Model deployment",
            WARN,
            message,
            remediation,
            import_error,
        )

    names, error = list_deployments_with_projects_sdk(endpoint, default_credential)
    if names is None:
        if agent_framework_installed():
            return CheckResult(
                "Model deployment",
                WARN,
                "agent-framework is installed, but deployments could not be listed",
                "Install/upgrade azure-ai-projects or verify the model in Azure AI Foundry before running the assistant.",
                error,
            )
        return CheckResult(
            "Model deployment",
            WARN,
            "Azure AI Projects SDK not installed; skipping model deployment lookup",
            f"Install requirements first: `{REQUIREMENTS_HINT}`",
            error,
        )

    if model_name in names:
        return CheckResult("Model deployment", PASS, f"Model deployment `{model_name}` exists")

    known = ", ".join(names[:10]) if names else "no deployments returned"
    return CheckResult(
        "Model deployment",
        FAIL,
        f"Model deployment `{model_name}` was not found",
        "Create or correct the model deployment in Azure AI Foundry, then update AZURE_AI_MODEL_DEPLOYMENT_NAME in `.env`.",
        f"Deployments seen: {known}",
    )


def check_config_vars(config: EnvConfig, name: str, keys: Iterable[str], remediation: str) -> CheckResult:
    missing = config.missing_from_config(keys)
    if missing:
        return CheckResult(name, FAIL, f"Missing {', '.join(missing)}", remediation)
    return CheckResult(name, PASS, f"{name} configured")


def check_mcp_vars(config: EnvConfig) -> CheckResult:
    return check_config_vars(
        config,
        "MCP configuration",
        STEP3_ENV_VARS,
        "Set MCP_SERVER_LABEL and MCP_SERVER_URL in `.env` before running step 3.",
    )


def check_search_vars(config: EnvConfig) -> CheckResult:
    return check_config_vars(
        config,
        "Azure AI Search configuration",
        STEP5_ENV_VARS,
        "Set AZURE_AI_SEARCH_ENDPOINT and AZURE_AI_SEARCH_INDEX_NAME in `.env` before running step 5.",
    )


def search_indexes_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return f"{base}/indexes?api-version={SEARCH_API_VERSION}"


def check_search_endpoint(config: EnvConfig) -> CheckResult:
    endpoint = config.get("AZURE_AI_SEARCH_ENDPOINT")
    if not endpoint:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            "AZURE_AI_SEARCH_ENDPOINT is not set",
            "Set AZURE_AI_SEARCH_ENDPOINT in `.env` before running step 5.",
        )

    parsed, url_error = validate_url(endpoint)
    if not parsed:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            "AZURE_AI_SEARCH_ENDPOINT is not a valid URL",
            f"Fix AZURE_AI_SEARCH_ENDPOINT in `.env`. {url_error}",
        )

    resolves, dns_error = check_host_resolves(parsed)
    if not resolves:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            f"Search host {parsed.hostname} does not resolve",
            "Check the search endpoint value, VPN/network access, and DNS, then re-run this script.",
            dns_error,
        )

    default_credential, import_error = import_default_credential()
    if default_credential is None:
        message, remediation = azure_identity_warning("; skipping authenticated Search probe")
        return CheckResult(
            "Azure AI Search endpoint",
            WARN,
            message,
            remediation,
            import_error,
        )

    token, token_error = get_token(default_credential, SEARCH_SCOPE)
    if not token:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            "Could not get an Azure AI Search token with DefaultAzureCredential",
            "Run `az login` and ensure your account has Search data-plane access, then re-run this script.",
            token_error,
        )

    status, _body, get_error = authenticated_get(search_indexes_url(endpoint), token)
    if status is None:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            "Azure AI Search endpoint is not reachable",
            "Check AZURE_AI_SEARCH_ENDPOINT, network access, and Azure service health.",
            get_error,
        )
    if status in {401, 403}:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            f"Azure AI Search returned HTTP {status}",
            "Grant your signed-in account Search Index Data Reader/Contributor access, then re-run this script.",
            get_error,
        )
    if status == 404:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            "Azure AI Search endpoint returned HTTP 404",
            "Confirm AZURE_AI_SEARCH_ENDPOINT points to the Search service endpoint.",
            get_error,
        )
    if status >= 500:
        return CheckResult(
            "Azure AI Search endpoint",
            FAIL,
            f"Azure AI Search returned HTTP {status}",
            "Retry later or check Azure service health for Azure AI Search.",
            get_error,
        )
    return CheckResult("Azure AI Search endpoint", PASS, f"Azure AI Search endpoint reachable (HTTP {status})")


def check_memory_store(config: EnvConfig) -> CheckResult:
    memory_store_name = config.get("MEMORY_STORE_NAME")
    if memory_store_name:
        return CheckResult("Memory store", PASS, "MEMORY_STORE_NAME is set")

    endpoint = config.get("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        return CheckResult(
            "Memory store",
            FAIL,
            "MEMORY_STORE_NAME is not set and project endpoint is unavailable",
            "Set MEMORY_STORE_NAME or set AZURE_AI_PROJECT_ENDPOINT and run the step 9 provisioning script.",
        )

    parsed, url_error = validate_url(endpoint)
    if not parsed:
        return CheckResult(
            "Memory store",
            FAIL,
            "MEMORY_STORE_NAME is not set and project endpoint URL is invalid",
            f"Fix AZURE_AI_PROJECT_ENDPOINT, then run the step 9 provisioning script. {url_error}",
        )
    resolves, dns_error = check_host_resolves(parsed)
    if not resolves:
        return CheckResult(
            "Memory store",
            FAIL,
            "MEMORY_STORE_NAME is not set and project host does not resolve",
            "Fix network/DNS access, then run the step 9 provisioning script.",
            dns_error,
        )

    default_credential, import_error = import_default_credential()
    if default_credential is None:
        identity_message, identity_remediation = azure_identity_warning()
        return CheckResult(
            "Memory store",
            WARN,
            f"MEMORY_STORE_NAME is not set and {identity_message}",
            f"{identity_remediation} Then run the step 9 provisioning script.",
            import_error,
        )

    token, token_error = get_token(default_credential, AI_SCOPE)
    if not token:
        return CheckResult(
            "Memory store",
            FAIL,
            "MEMORY_STORE_NAME is not set and project credentials are not working",
            "Run `az login`, then run the step 9 provisioning script to create a memory store.",
            token_error,
        )

    return CheckResult(
        "Memory store",
        WARN,
        "MEMORY_STORE_NAME is not set, but the project appears reachable for provisioning",
        "Run the step 9 provisioning script (for example, `python travel_assistant/provision_memory_store.py`) and add MEMORY_STORE_NAME to `.env`.",
    )


def checks_for_step(step: int) -> list[Callable[[EnvConfig], CheckResult]]:
    checks: list[Callable[[EnvConfig], CheckResult]] = [
        check_python_version,
        check_az_cli,
        check_az_login,
        check_env_file,
        check_project_endpoint,
        check_model_deployment,
    ]
    if step == 3:
        checks.append(check_mcp_vars)
    if step >= 5:
        checks.extend([check_search_vars, check_search_endpoint])
    if step >= 9:
        checks.append(check_memory_store)
    return checks


def safe_run(check: Callable[[EnvConfig], CheckResult], config: EnvConfig) -> CheckResult:
    try:
        return check(config)
    except Exception:
        name = getattr(check, "__name__", "preflight check").replace("check_", "").replace("_", " ")
        return CheckResult(
            name,
            FAIL,
            "Unexpected preflight error",
            "Re-run with `--verbose`; if this persists, file a workshop issue with the trace.",
            raw_exception(),
        )


def print_human(results: list[CheckResult], step: int, verbose: bool, config: EnvConfig) -> None:
    colors = Colors(enabled="NO_COLOR" not in os.environ)
    for result in results:
        symbol = "✅"
        line = f"{symbol} {result.message}"
        if result.status == WARN:
            symbol = "⚠️ "
            line = f"{symbol} {result.message}"
            if result.remediation:
                line += f" — {result.remediation}"
            line = colors.yellow(line)
        elif result.status == FAIL:
            symbol = "❌"
            line = f"{symbol} {result.message}"
            if result.remediation:
                line += f" — {result.remediation}"
            line = colors.red(line)
        else:
            line = colors.green(line)
        print(line)
        if verbose and result.raw_error:
            print(indent_trace(result.raw_error))

    passed, warnings, failed = summarize(results)
    summary = f"Preflight: {passed} passed, {warnings} warning{'s' if warnings != 1 else ''}, {failed} failed."
    if failed:
        print(colors.red(summary))
        print(f"Suggested next command: python .workshop/scripts/preflight.py{step_arg(step)} --verbose")
    else:
        print(colors.green(summary) if warnings == 0 else colors.yellow(summary))
        next_cmd = next_command_for_step(step, config)
        if next_cmd:
            print(f"Suggested next command: {next_cmd}")
        else:
            print(f"Suggested next: {next_hint_for_step(step)}")


def indent_trace(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def step_arg(step: int) -> str:
    return f" --step {step}" if step else ""


def next_command_for_step(step: int, config: EnvConfig) -> str:
    """Shell command to run next after a passing preflight for the given step.

    Returns an empty string when the next action is not a single command — after
    Step 0 (environment setup) the learner opens README.md to begin Step 1, and
    after Step 1 they continue to the README's "Run and deploy TravelBuddy"
    section (which walks through the azd/Toolkit flow rather than one command).
    """
    if step == 0:
        return ""
    if step == 1:
        # Step 1 runs and deploys through the README's guided azd/Toolkit flow,
        # not a single command, so point the learner to that section instead.
        return ""
    # Steps 2+ smoke-test the agent locally before deploying.
    return "python travel_assistant/main.py"


def next_hint_for_step(step: int) -> str:
    """Human guidance to print when there's no single next command to run."""
    if step == 1:
        return "open README.md and continue to the 'Run and deploy TravelBuddy' section."
    return "open README.md and start Step 1."


def summarize(results: list[CheckResult]) -> tuple[int, int, int]:
    passed = sum(1 for result in results if result.status == PASS)
    warnings = sum(1 for result in results if result.status == WARN)
    failed = sum(1 for result in results if result.status == FAIL)
    return passed, warnings, failed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Azure AI Foundry workshop prerequisites.")
    parser.add_argument(
        "--step",
        type=int,
        default=0,
        help="Workshop step to validate cumulatively (default: 0).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output for CI.")
    parser.add_argument("--verbose", action="store_true", help="Show raw error traces for failed checks.")
    args = parser.parse_args(argv)
    if args.step < 0:
        parser.error("--step must be 0 or greater")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    config = EnvConfig(repo_root())
    results = [safe_run(check, config) for check in checks_for_step(args.step)]
    passed, warnings, failed = summarize(results)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": failed == 0,
                    "step": args.step,
                    "summary": {"passed": passed, "warnings": warnings, "failed": failed},
                    "results": [result.to_json(verbose=args.verbose) for result in results],
                    "suggested_next_command": (
                        f"python .workshop/scripts/preflight.py{step_arg(args.step)} --verbose"
                        if failed
                        else next_command_for_step(args.step, config)
                    ),
                },
                indent=2,
            )
        )
    else:
        print_human(results, args.step, args.verbose, config)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
