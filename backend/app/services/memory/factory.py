"""
Memory store factory.

Returns the active store implementations as singletons. Defaults to the
SQL-backed stores (persistent across restarts). Tests can override with the
in-memory stores via set_stores_for_testing().

The integration layer imports ONLY these getters, so swapping the backing
store never touches the pipeline code.
"""
from __future__ import annotations

from typing import Any

_procedure_store: Any = None
_episode_store: Any = None
_lesson_store: Any = None


def get_procedure_store() -> Any:
    global _procedure_store
    if _procedure_store is None:
        from app.services.memory.sql_store import SqlProcedureStore
        _procedure_store = SqlProcedureStore()
    return _procedure_store


def get_episode_store() -> Any:
    global _episode_store
    if _episode_store is None:
        from app.services.memory.sql_store import SqlEpisodeStore
        _episode_store = SqlEpisodeStore()
    return _episode_store


def get_lesson_store() -> Any:
    global _lesson_store
    if _lesson_store is None:
        from app.services.memory.sql_store import SqlLessonStore
        _lesson_store = SqlLessonStore()
    return _lesson_store


def set_stores_for_testing(*, procedure=None, episode=None, lesson=None) -> None:
    """Override the singletons (e.g. with in-memory stores) for tests."""
    global _procedure_store, _episode_store, _lesson_store
    if procedure is not None:
        _procedure_store = procedure
    if episode is not None:
        _episode_store = episode
    if lesson is not None:
        _lesson_store = lesson