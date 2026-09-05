"""Single source of truth for the platform version.

The number lives in the top-level ``VERSION`` file; ``pyproject.toml`` reads the
same file, and ``scripts/release.sh`` mirrors it into ``dashboard/package.json``.
Services can import ``__version__`` for FastAPI metadata or health endpoints.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read() -> str:
    if _VERSION_FILE.is_file():
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    try:  # installed as a distribution without the source tree
        return _dist_version("cancer-ai")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__: str = _read()

__all__ = ["__version__"]
