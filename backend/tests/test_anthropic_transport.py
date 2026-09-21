"""
Guards the Brotli workaround in the Anthropic client.

Without it, every Claude call fails with a misleading
`APIConnectionError: Connection error.` while the network is fine. The real
cause is upstream: `httpx2` advertises Brotli when the `brotli` package is
installed, then decodes with `process(data, output_buffer_limit=...)`, and
`brotli.Decompressor.process()` takes no keyword arguments.

Anthropic returns Brotli by default, so this is not an edge case — it breaks
100% of Claude calls on an affected install. Both the video pipeline and the
sandbox proxy must go through the patched constructor.
"""
from __future__ import annotations

import inspect

import pytest


def test_client_does_not_request_brotli():
    from app.core.llm.anthropic_client import build_anthropic_client

    client = build_anthropic_client("sk-ant-not-a-real-key")
    encoding = dict(client._client.headers).get("accept-encoding", "")
    assert "br" not in encoding.split(", "), (
        "Brotli must not be requested: httpx2's brotli decoder raises TypeError"
    )
    assert "gzip" in encoding, "gzip should still be used so responses stay compressed"


def test_pipeline_client_uses_the_patched_constructor():
    from app.core.llm.anthropic_client import AnthropicLLMClient

    src = inspect.getsource(AnthropicLLMClient.__init__)
    assert "build_anthropic_client" in src
    assert "anthropic.Anthropic(" not in src, (
        "construct through build_anthropic_client so the workaround applies"
    )


def test_sandbox_proxy_uses_the_patched_constructor():
    from app.api import sandbox_llm

    src = inspect.getsource(sandbox_llm._claude_generate)
    assert "build_anthropic_client" in src
    assert "anthropic.Anthropic(" not in src, (
        "the sandbox proxy must not build a raw client; it would hit the bug"
    )


@pytest.mark.parametrize("purpose,expected", [
    ("keyframe_understanding", "claude-haiku-4-5"),
    ("plan_synthesis", "claude-opus-5"),
    ("plan_correction", "claude-opus-5"),
    (None, "claude-sonnet-5"),
])
def test_each_pipeline_stage_can_use_its_own_model(purpose, expected):
    """Captioning is token-heavy but easy; plan synthesis is one hard call."""
    from app.config import Settings

    s = Settings(llm_provider="anthropic", llm_model="claude-sonnet-5",
                 caption_llm_model="claude-haiku-4-5",
                 plan_llm_model="claude-opus-5")
    assert s.model_for_purpose(purpose) == expected
