"""Tests for the azure-identity import handling in ``scripts/preflight.py``.

These tests cover the regression described in issue #3 — the preflight
warning misreported azure-identity as "not installed" whenever any kind of
import failure happened. They exercise:

* the success path (credential class returned, no error),
* the ``ModuleNotFoundError`` path (package genuinely not installed),
* a generic-exception path (e.g. a partially broken install),
* the single-cache invariant (one import attempt per process), and
* one integration check (``check_project_endpoint``) to confirm the new
  warning message includes ``sys.executable`` so wrong-venv cases are easier
  to diagnose.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

import scripts.preflight as preflight


@pytest.fixture(autouse=True)
def clear_import_cache():
    """Ensure each test starts with a clean import cache."""
    preflight.reset_import_default_credential_cache()
    yield
    preflight.reset_import_default_credential_cache()


def _install_fake_azure_identity(monkeypatch: pytest.MonkeyPatch) -> type:
    """Install a stub ``azure.identity`` module exposing
    ``DefaultAzureCredential`` and return the stub class.
    """

    class _FakeDefaultAzureCredential:  # pragma: no cover - identity only
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    azure_pkg = types.ModuleType("azure")
    azure_pkg.__path__ = []  # mark as package
    identity_mod = types.ModuleType("azure.identity")
    identity_mod.DefaultAzureCredential = _FakeDefaultAzureCredential
    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    return _FakeDefaultAzureCredential


def _force_module_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``from azure.identity import DefaultAzureCredential`` raise
    ``ModuleNotFoundError`` regardless of whether the real package is
    installed in the test environment.
    """
    # Remove any cached real/fake modules first so the meta path runs.
    for name in ("azure.identity", "azure"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "azure.identity" or name.startswith("azure.identity."):
            raise ModuleNotFoundError("No module named 'azure'")
        if name == "azure" and fromlist:
            raise ModuleNotFoundError("No module named 'azure'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)


def _force_generic_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the import fail with a non-ImportError exception, simulating a
    half-broken install (e.g. native dependency failing at import time).
    """
    for name in ("azure.identity", "azure"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "azure.identity" and fromlist and "DefaultAzureCredential" in fromlist:
            raise RuntimeError("simulated broken native dependency")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)


# ---------------------------------------------------------------------------
# import_default_credential
# ---------------------------------------------------------------------------


def test_import_default_credential_returns_class_when_importable(monkeypatch):
    fake_cls = _install_fake_azure_identity(monkeypatch)

    credential_cls, error = preflight.import_default_credential()

    assert credential_cls is fake_cls
    assert error == ""


def test_import_default_credential_reports_module_not_found(monkeypatch):
    _force_module_not_found(monkeypatch)

    credential_cls, error = preflight.import_default_credential()

    assert credential_cls is None
    assert "ModuleNotFoundError" in error
    assert preflight.azure_identity_import_failed(error) is True


def test_import_default_credential_reports_generic_exception(monkeypatch):
    _force_generic_import_error(monkeypatch)

    credential_cls, error = preflight.import_default_credential()

    assert credential_cls is None
    assert "RuntimeError" in error
    assert "simulated broken native dependency" in error
    # A non-ImportError should NOT be classified as a missing package.
    assert preflight.azure_identity_import_failed(error) is False


def test_import_default_credential_is_cached(monkeypatch):
    call_counter = {"count": 0}
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    for name in ("azure.identity", "azure"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    def counting_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "azure.identity" and fromlist and "DefaultAzureCredential" in fromlist:
            call_counter["count"] += 1
            raise ModuleNotFoundError("No module named 'azure'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", counting_import)

    for _ in range(4):
        preflight.import_default_credential()

    assert call_counter["count"] == 1


# ---------------------------------------------------------------------------
# azure_identity_warning
# ---------------------------------------------------------------------------


def test_azure_identity_warning_includes_interpreter_and_remediation():
    message, remediation = preflight.azure_identity_warning()

    assert sys.executable in message
    assert "azure-identity not importable" in message
    assert sys.executable in remediation
    assert "pip install -r travel_assistant/requirements.txt" in remediation


def test_azure_identity_warning_appends_context_suffix():
    message, _ = preflight.azure_identity_warning("; skipping model deployment lookup")

    assert message.endswith("; skipping model deployment lookup")
    assert sys.executable in message


# ---------------------------------------------------------------------------
# Integration: check_project_endpoint surfaces the new wording
# ---------------------------------------------------------------------------


class _StubEnvConfig:
    """Minimal stand-in for ``EnvConfig`` that returns the supplied values."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, key: str) -> str:
        return self._values.get(key, "")


def test_check_project_endpoint_warning_includes_sys_executable(monkeypatch):
    _force_module_not_found(monkeypatch)

    # Bypass DNS so the check reaches the import step.
    monkeypatch.setattr(preflight, "check_host_resolves", lambda parsed: (True, ""))

    config = _StubEnvConfig({"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/"})
    result = preflight.check_project_endpoint(config)

    assert result.status == preflight.WARN
    assert "azure-identity not importable" in result.message
    assert sys.executable in result.message
    assert sys.executable in result.remediation
    assert "ModuleNotFoundError" in result.raw_error


def test_check_memory_store_warning_reuses_interpreter_aware_remediation(monkeypatch):
    _force_module_not_found(monkeypatch)
    monkeypatch.setattr(preflight, "check_host_resolves", lambda parsed: (True, ""))

    config = _StubEnvConfig({"AZURE_AI_PROJECT_ENDPOINT": "https://example.invalid/"})
    result = preflight.check_memory_store(config)

    assert result.status == preflight.WARN
    assert "azure-identity not importable" in result.message
    # Remediation must reference the active interpreter, not just a bare
    # `pip install -r ...` hint.
    assert sys.executable in result.remediation
    assert "step 9 provisioning script" in result.remediation


def test_project_probe_url_appends_api_version():
    assert (
        preflight.project_probe_url("https://example.services.ai.azure.com/api/projects/demo")
        == "https://example.services.ai.azure.com/api/projects/demo/deployments?api-version=v1"
    )


def test_project_probe_url_preserves_existing_query():
    assert (
        preflight.project_probe_url("https://example.services.ai.azure.com/api/projects/demo?foo=bar")
        == "https://example.services.ai.azure.com/api/projects/demo/deployments?foo=bar&api-version=v1"
    )


def test_project_probe_url_keeps_existing_api_version():
    assert (
        preflight.project_probe_url("https://example.services.ai.azure.com/api/projects/demo?api-version=2025-05-01")
        == "https://example.services.ai.azure.com/api/projects/demo/deployments?api-version=2025-05-01"
    )


def _prime_project_endpoint(monkeypatch, status):
    """Drive ``check_project_endpoint`` down to the HTTP probe and force a
    specific status code, bypassing DNS, credential import, and token calls.
    """
    _install_fake_azure_identity(monkeypatch)
    monkeypatch.setattr(preflight, "check_host_resolves", lambda parsed: (True, ""))
    monkeypatch.setattr(preflight, "get_token", lambda credential, scope: ("token", ""))
    monkeypatch.setattr(
        preflight, "authenticated_get", lambda url, token, timeout=15: (status, "", "")
    )


def test_check_project_endpoint_passes_on_2xx(monkeypatch):
    _prime_project_endpoint(monkeypatch, 200)
    config = _StubEnvConfig(
        {"AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo"}
    )
    result = preflight.check_project_endpoint(config)
    assert result.status == preflight.PASS


def test_check_project_endpoint_fails_on_unexpected_status(monkeypatch):
    # A 400 (e.g. bad api-version) must not be reported as reachable.
    _prime_project_endpoint(monkeypatch, 400)
    config = _StubEnvConfig(
        {"AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo"}
    )
    result = preflight.check_project_endpoint(config)
    assert result.status == preflight.FAIL
    assert "HTTP 400" in result.message


def test_check_project_endpoint_fails_on_404(monkeypatch):
    _prime_project_endpoint(monkeypatch, 404)
    config = _StubEnvConfig(
        {"AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo"}
    )
    result = preflight.check_project_endpoint(config)
    assert result.status == preflight.FAIL
    assert "404" in result.message
