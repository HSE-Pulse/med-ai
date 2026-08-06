"""Centralised configuration for the cancer-AI monorepo."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Every field can be overridden by setting the corresponding env var
    (case-insensitive).  For example ``MONGO_URI=mongodb://remote:27017/``
    overrides the default connection string.
    """

    # -- Database --------------------------------------------------------------
    MONGO_URI: str = "mongodb://localhost:27017/"

    # -- Paths -----------------------------------------------------------------
    DATA_DIR: Path = Path("/home/hari/hse/datasets")
    MODEL_DIR: Path = Path("/home/hari/hse/models")

    # -- Logging ---------------------------------------------------------------
    LOG_LEVEL: str = "INFO"

    # -- API -------------------------------------------------------------------
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The first call reads environment variables (and an optional ``.env``
    file); subsequent calls return the same object.
    """
    return Settings()
