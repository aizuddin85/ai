"""
Pytest fixtures and configuration.
Sets mandatory env vars so Settings validation passes without real credentials.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject stub environment variables before every test."""
    monkeypatch.setenv("AZURE_TENANT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_IDS", "11111111-1111-1111-1111-111111111111")
    # Remove the old singular env var so it does not shadow the list field
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setenv("AZURE_FOUNDRY_ENDPOINT", "https://test-project.services.ai.azure.com/models")
    monkeypatch.setenv("AZURE_FOUNDRY_MODEL", "gpt-4o")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "console")
    # Clear any real credentials so tests always use mock paths
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)
    # MCP_TRANSPORT and AZURE_ARM_TOKEN were used by the old custom Python
    # MCP server and are no longer part of the configuration model.
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("AZURE_ARM_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """
    Clear all lru_cache singletons before and after every test so that
    monkeypatched env vars are picked up by Settings/ApiSettings and no
    cached state leaks between tests.
    """
    from server.config import get_settings
    from api.config import get_api_settings
    get_settings.cache_clear()
    get_api_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_api_settings.cache_clear()
