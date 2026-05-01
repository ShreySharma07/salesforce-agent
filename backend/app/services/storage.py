"""
Storage abstraction. Reads and writes files; backend is local fs for dev,
S3 for production. The contract is intentionally minimal so swapping is
trivial.

Keys are POSIX-style paths even on Windows ("videos/abc123.mp4").
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.config import get_settings


class Storage(ABC):
    @abstractmethod
    def write_bytes(self, key: str, data: bytes) -> str:
        """Store bytes at `key`. Returns the canonical URL/path."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """Read all bytes from `key`. Raises FileNotFoundError if missing."""

    @abstractmethod
    def write_text(self, key: str, text: str) -> str:
        """Store text. Returns the canonical URL/path."""

    @abstractmethod
    def read_text(self, key: str) -> str:
        """Read text from `key`. Raises FileNotFoundError if missing."""

    @abstractmethod
    def local_path(self, key: str) -> Path:
        """Return a local filesystem path for the key.
        For LocalStorage this is the actual file. For S3 this would
        download to a temp file. Used when third-party tools (ffmpeg)
        need an actual file path, not bytes."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalStorage(Storage):
    """Files live under settings.local_storage_root. Good for dev."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or get_settings().local_storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _full(self, key: str) -> Path:
        # Defend against path-traversal: resolve and ensure within root
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"key {key!r} escapes storage root")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_bytes(self, key: str, data: bytes) -> str:
        p = self._full(key)
        p.write_bytes(data)
        return str(p)

    def read_bytes(self, key: str) -> bytes:
        return self._full(key).read_bytes()

    def write_text(self, key: str, text: str) -> str:
        p = self._full(key)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def read_text(self, key: str) -> str:
        return self._full(key).read_text(encoding="utf-8")

    def local_path(self, key: str) -> Path:
        return self._full(key)

    def exists(self, key: str) -> bool:
        return self._full(key).exists()


def get_storage() -> Storage:
    """Return the configured storage backend. For now always LocalStorage;
    swap to an S3 implementation when we deploy."""
    return LocalStorage()