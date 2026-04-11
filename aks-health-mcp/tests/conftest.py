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
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "console")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    # Clear any real SP credentials so tests always use mock paths
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Clear the lru_cache on Settings so env changes take effect."""
    from server.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
