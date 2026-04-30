"""Verify the mock LLM client works through the abstraction."""
from app.core.llm import MockLLMClient
from app.core.llm.factory import get_llm_client


def test_mock_returns_canned_response():
    client = MockLLMClient()
    response = client.generate("any prompt", purpose="keyframe_understanding")
    assert "Salesforce" in response.text
    assert response.usage.cost_usd == 0.0
    assert response.usage.provider == "mock"


def test_mock_unknown_purpose():
    client = MockLLMClient()
    response = client.generate("any prompt", purpose="something_unknown")
    assert "MOCK RESPONSE" in response.text


def test_factory_returns_mock_by_default():
    """With LLM_PROVIDER=mock (the default), factory yields MockLLMClient."""
    client = get_llm_client()
    assert isinstance(client, MockLLMClient)