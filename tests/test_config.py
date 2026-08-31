"""Settings behaviour, including the live-key refusal promised in SECURITY.md."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from reclaimos.config import RazorpayCredentials, Settings, settings


def test_defaults_work_with_no_env_file() -> None:
    """A clean clone must be runnable without a .env — this is a judging axis."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.database_url.startswith("sqlite:///")
    assert s.max_attempts == 4
    assert s.redis_url is None


def test_settings_carry_no_credential_fields() -> None:
    """`reclaimos config` prints every field, so no field may be a secret."""
    forbidden = {"key", "secret", "token", "password"}
    for name in settings.model_dump():
        assert not any(word in name for word in forbidden), f"{name} looks like a secret"


def test_razorpay_live_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="test key"):
        RazorpayCredentials(key_id="rzp_live_abc123", key_secret="x", _env_file=None)  # type: ignore[call-arg]


def test_razorpay_test_key_is_accepted() -> None:
    creds = RazorpayCredentials(key_id="rzp_test_abc123", key_secret="x", _env_file=None)  # type: ignore[call-arg]
    assert creds.key_id == "rzp_test_abc123"
