"""
Shared test configuration.

Three things every test in this suite depends on:

1. `sandbox_agent` is importable. It lives at the REPO ROOT (it ships inside
   the container image, not inside the backend package), so the root has to be
   on sys.path for the grounding/executor tests to import it.

2. No test ever calls a real LLM or touches the developer's database. The
   provider is forced to `mock` and storage/DB are redirected into a per-session
   temp directory, so running the suite costs nothing and cannot corrupt
   `.local_storage/dev.db`.

3. Settings are cached with `lru_cache`, so the env has to be set BEFORE the
   first `get_settings()` call and the cache cleared around it.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

for path in (str(REPO_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _isolate_environment() -> None:
    """Point the whole test session at a throwaway DB, storage root and LLM."""
    tmp = tempfile.mkdtemp(prefix="agent_tests_")
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["LLM_MODEL"] = "mock-model"
    os.environ["LOCAL_STORAGE_ROOT"] = tmp
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/test.db"
    os.environ["AUTO_MIGRATE"] = "false"
    os.environ.setdefault("VAULT_ENCRYPTION_KEY", _throwaway_fernet_key())
    # Never inherit a real key from the developer's shell.
    for leaky in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(leaky, None)


def _throwaway_fernet_key() -> str:
    """A per-session vault key so credential tests can encrypt/decrypt."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


_isolate_environment()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Rebuild Settings around every test.

    Tests that monkeypatch env vars (storage root, provider) would otherwise
    read a Settings object cached by an earlier test.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only (no trio in this project)."""
    return "asyncio"
