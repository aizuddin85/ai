"""Tests for settings and configuration validation."""
from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from server.config import Settings


def test_settings_with_sp_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-id-123")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "super-secret")
    s = Settings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        anthropic_api_key="sk-ant-test",
    )
    assert s.uses_service_principal is True
    # Secret must not be exposed via str()
    assert "super-secret" not in str(s.azure_client_secret)


def test_settings_without_sp_credentials() -> None:
    s = Settings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        anthropic_api_key="sk-ant-test",
    )
    assert s.uses_service_principal is False


def test_invalid_tenant_id_rejected() -> None:
    with pytest.raises(ValidationError, match="valid GUID"):
        Settings(
            azure_tenant_id="not-a-guid",
            anthropic_api_key="sk-ant-test",
        )


def test_missing_tenant_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Remove the autouse fixture's value so Settings must rely on the argument
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    with pytest.raises(ValidationError):
        # tenant_id is required and not in env → must raise
        Settings(anthropic_api_key="sk-ant-test")  # type: ignore[call-arg]


def test_log_level_validation() -> None:
    s = Settings(
        azure_tenant_id="00000000-0000-0000-0000-000000000000",
        anthropic_api_key="sk-ant-test",
        log_level="WARNING",
    )
    assert s.log_level == "WARNING"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            azure_tenant_id="00000000-0000-0000-0000-000000000000",
            anthropic_api_key="sk-ant-test",
            log_level="VERBOSE",  # type: ignore[arg-type]
        )
