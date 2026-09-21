"""
The video pipeline and the sandbox ReAct loop can use different models.

They are different jobs: the pipeline makes a few long vision calls once per
recording, while the loop makes many small strict-JSON calls on every run.
Before this split a single LLM_PROVIDER forced both onto the same model.
"""
from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**kw) -> Settings:
    base = dict(llm_provider="gemini", llm_model="gemini-3.1-pro-preview")
    return Settings(**{**base, **kw})


def test_sandbox_defaults_to_the_pipeline_model():
    """Unset sandbox_* keeps single-model setups working unchanged."""
    s = _settings()
    assert s.effective_sandbox_provider() == "gemini"
    assert s.effective_sandbox_model() == "gemini-3.1-pro-preview"


def test_sandbox_can_use_a_different_provider():
    s = _settings(sandbox_llm_provider="anthropic", sandbox_llm_model="claude-opus-5")
    # Pipeline is untouched...
    assert s.llm_provider == "gemini"
    assert s.llm_model == "gemini-3.1-pro-preview"
    # ...while the loop runs on Claude.
    assert s.effective_sandbox_provider() == "anthropic"
    assert s.effective_sandbox_model() == "claude-opus-5"


def test_mismatched_provider_without_a_model_is_refused():
    """Falling back here would send a Gemini model id to Anthropic."""
    s = _settings(sandbox_llm_provider="anthropic")
    with pytest.raises(ValueError, match="SANDBOX_LLM_MODEL must be set"):
        s.effective_sandbox_model()


def test_same_provider_may_still_override_only_the_model():
    """A cheaper model for the loop than for plan synthesis is valid."""
    s = _settings(sandbox_llm_model="gemini-2.5-flash")
    assert s.effective_sandbox_provider() == "gemini"
    assert s.effective_sandbox_model() == "gemini-2.5-flash"


def test_container_is_told_the_sandbox_model_and_no_keys():
    s = _settings(sandbox_llm_provider="anthropic", sandbox_llm_model="claude-opus-5",
                  gemini_api_key="AIzaFAKE", anthropic_api_key="sk-ant-FAKE")
    env = s.llm_env_for_sandbox()
    assert env == {"LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-opus-5"}
    # The container must never receive key material.
    blob = " ".join(env.values())
    assert "AIza" not in blob and "sk-ant" not in blob


def test_proxy_routes_to_the_sandbox_provider(monkeypatch):
    """The /sandbox/llm route dispatches on the SANDBOX provider."""
    from app.api import sandbox_llm
    from app.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.1-pro-preview")
    monkeypatch.setenv("SANDBOX_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("SANDBOX_LLM_MODEL", "claude-opus-5")
    get_settings.cache_clear()

    s = get_settings()
    chosen = sandbox_llm._claude_generate if s.effective_sandbox_provider() == "anthropic" \
        else sandbox_llm._gemini_generate
    assert chosen is sandbox_llm._claude_generate

    get_settings.cache_clear()
