"""Tests for settings and configuration validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.config import Settings

_BASE = dict(
    azure_tenant_id="00000000-0000-0000-0000-000000000000",
    azure_foundry_endpoint="https://test.services.ai.azure.com/models",
)


def test_settings_with_sp_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "super-secret")
    s = Settings(**_BASE)  # type: ignore[arg-type]
    assert s.uses_service_principal is True
    # Secret must not be exposed in str repr
    assert "super-secret" not in str(s.azure_client_secret)


def test_settings_without_sp_credentials() -> None:
    s = Settings(**_BASE)  # type: ignore[arg-type]
    assert s.uses_service_principal is False


def test_settings_with_foundry_key() -> None:
    s = Settings(**_BASE, azure_foundry_api_key="my-api-key")  # type: ignore[arg-type]
    assert s.uses_foundry_key_auth is True
    # Key must not be exposed in str repr
    assert "my-api-key" not in str(s.azure_foundry_api_key)


def test_settings_without_foundry_key() -> None:
    s = Settings(**_BASE)  # type: ignore[arg-type]
    assert s.uses_foundry_key_auth is False


def test_invalid_tenant_id_rejected() -> None:
    with pytest.raises(ValidationError, match="valid GUID"):
        Settings(
            azure_tenant_id="not-a-guid",
            azure_foundry_endpoint="https://test.services.ai.azure.com/models",
        )


def test_missing_tenant_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    with pytest.raises(ValidationError):
        Settings(azure_foundry_endpoint="https://test.services.ai.azure.com/models")  # type: ignore[call-arg]


def test_missing_foundry_endpoint_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_FOUNDRY_ENDPOINT", raising=False)
    with pytest.raises(ValidationError):
        Settings(azure_tenant_id="00000000-0000-0000-0000-000000000000")  # type: ignore[call-arg]


def test_log_level_validation() -> None:
    s = Settings(**_BASE, log_level="WARNING")  # type: ignore[arg-type]
    assert s.log_level == "WARNING"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**_BASE, log_level="VERBOSE")  # type: ignore[arg-type]


def test_missing_foundry_model_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """AZURE_FOUNDRY_MODEL is required — no hardcoded default exists."""
    monkeypatch.delenv("AZURE_FOUNDRY_MODEL", raising=False)
    with pytest.raises(ValidationError):
        Settings(**_BASE)  # type: ignore[call-arg]


def test_foundry_model_read_from_env() -> None:
    """Model name is read from AZURE_FOUNDRY_MODEL env var (set by conftest)."""
    s = Settings(**_BASE)  # type: ignore[arg-type]
    assert s.azure_foundry_model == "gpt-4o"  # value injected by conftest


def test_custom_foundry_model() -> None:
    s = Settings(**_BASE, azure_foundry_model="gpt-4o-mini")  # type: ignore[arg-type]
    assert s.azure_foundry_model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# azure_subscription_ids
# ---------------------------------------------------------------------------


def test_subscription_ids_single_value() -> None:
    """A single subscription ID is parsed into a one-element list via the property."""
    s = Settings(**_BASE, azure_subscription_ids="11111111-1111-1111-1111-111111111111")  # type: ignore[arg-type]
    assert s.subscription_ids == ["11111111-1111-1111-1111-111111111111"]
    assert s.azure_subscription_id == "11111111-1111-1111-1111-111111111111"


def test_subscription_ids_comma_separated() -> None:
    """Comma-separated string is parsed into a list."""
    val = "11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222"
    s = Settings(**_BASE, azure_subscription_ids=val)  # type: ignore[arg-type]
    assert len(s.subscription_ids) == 2
    assert "11111111-1111-1111-1111-111111111111" in s.subscription_ids
    assert "22222222-2222-2222-2222-222222222222" in s.subscription_ids


def test_subscription_ids_comma_separated_with_spaces() -> None:
    """Whitespace around commas is stripped."""
    val = "11111111-1111-1111-1111-111111111111 , 22222222-2222-2222-2222-222222222222"
    s = Settings(**_BASE, azure_subscription_ids=val)  # type: ignore[arg-type]
    assert s.subscription_ids == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


def test_subscription_ids_empty_defaults_to_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AZURE_SUBSCRIPTION_IDS is absent, the list is empty."""
    monkeypatch.delenv("AZURE_SUBSCRIPTION_IDS", raising=False)
    s = Settings(**_BASE)  # type: ignore[arg-type]
    assert s.subscription_ids == []
    assert s.azure_subscription_id is None


def test_subscription_id_property_returns_first() -> None:
    """azure_subscription_id backward-compat property returns first element."""
    val = "11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222"
    s = Settings(**_BASE, azure_subscription_ids=val)  # type: ignore[arg-type]
    assert s.azure_subscription_id == "11111111-1111-1111-1111-111111111111"
