"""Shared fixtures for the Chef test suite."""

from __future__ import annotations

from typing import Any

import pytest

from chef.config import ChefSettings


@pytest.fixture()
def make_settings(tmp_path: Any) -> Any:
    """Factory for isolated settings that never read the developer's .env."""

    def _make(**overrides: Any) -> ChefSettings:
        defaults: dict[str, Any] = {
            "llm_enabled": False,
            "llm_api_key": "",
            "child_cwd": str(tmp_path),
            "audit_log_path": str(tmp_path / "audit.log"),
            "expect_timeout": 5.0,
            "max_idle_timeouts": 2,
            "llm_backoff_base": 0.01,  # keep retry tests fast
        }
        defaults.update(overrides)
        return ChefSettings(_env_file=None, **defaults)

    return _make
