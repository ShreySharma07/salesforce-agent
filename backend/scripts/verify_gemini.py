"""
ONE-SHOT Gemini verification. Makes exactly 1 API call to confirm the
LLMClient abstraction works with the real Gemini provider.

Uses your free-tier quota (1 call per run). Run sparingly.

Usage:
    cd backend
    source .venv/bin/activate
    python -m scripts.verify_gemini
"""
import sys

from app.config import get_settings
from app.core.llm.gemini import GeminiLLMClient


def main() -> int:
    settings = get_settings()

    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return 1

    print(f"Provider: gemini (overriding {settings.llm_provider})")
    print(f"Model:    gemini-2.5-flash")
    print(f"Calling Gemini with a tiny prompt to verify wiring...\n")

    client = GeminiLLMClient(
        model="gemini-2.5-flash",
        api_key=settings.gemini_api_key,
    )

    response = client.generate(
        prompt="Reply with exactly the word: OK",
        purpose="verification_test",
        max_tokens=200,
    )

    print(f"Response text:    {response.text!r}")
    print(f"Input tokens:     {response.usage.input_tokens}")
    print(f"Output tokens:    {response.usage.output_tokens}")
    print(f"Latency:          {response.usage.latency_ms}ms")
    print(f"Cost:             ${response.usage.cost_usd:.6f}")
    print(f"Provider field:   {response.usage.provider}")

    if "OK" in response.text.upper():
        print("\n✅ Gemini wiring verified end-to-end.")
        return 0
    else:
        print("\n⚠️  Got a response but text didn't contain 'OK'. Check above.")
        return 0


if __name__ == "__main__":
    sys.exit(main())