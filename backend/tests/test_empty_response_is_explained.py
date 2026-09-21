"""
An empty model response must never pass silently.

Gemini 2.5+/3.x models think, and thinking tokens are charged against
`max_output_tokens`. On a tight budget the model can spend the whole
allowance reasoning and return no visible text: the HTTP call succeeds,
usage is non-zero, and `text` is "".

That empty string used to flow straight into the pipeline, producing blank
captions and unparseable plan JSON. The only symptom was "Gemini is not
working", with nothing in the logs pointing at the cause.
"""
from __future__ import annotations

from app.core.llm.gemini import _explain_empty_gemini_response


class _Usage:
    def __init__(self, thoughts): self.thoughts_token_count = thoughts


class _Finish:
    def __init__(self, name): self.name = name


class _Cand:
    def __init__(self, finish): self.finish_reason = _Finish(finish)


class _Raw:
    def __init__(self, finish, thoughts):
        self.usage_metadata = _Usage(thoughts)
        self.candidates = [_Cand(finish)]


def test_truncation_names_thinking_as_the_cause():
    msg = _explain_empty_gemini_response(_Raw("MAX_TOKENS", 5), "gemini-3.5-flash", 8)
    assert "max_output_tokens=8" in msg
    assert "5 tokens on internal thinking" in msg
    assert "Raise max_tokens" in msg


def test_safety_block_is_reported_as_such():
    msg = _explain_empty_gemini_response(_Raw("SAFETY", 0), "gemini-3.5-flash", 4096)
    assert "blocked" in msg and "SAFETY" in msg


def test_unknown_reason_still_reports_what_is_known():
    msg = _explain_empty_gemini_response(_Raw("OTHER", 12), "gemini-3.5-flash", 512)
    assert "OTHER" in msg and "12" in msg


def test_both_gemini_call_sites_guard_against_empty_text():
    """The pipeline client and the sandbox proxy must both raise, not return ''."""
    import inspect
    from app.api import sandbox_llm
    from app.core.llm.gemini import GeminiLLMClient

    for src in (inspect.getsource(GeminiLLMClient.generate),
                inspect.getsource(sandbox_llm._gemini_generate)):
        assert "_explain_empty_gemini_response" in src
