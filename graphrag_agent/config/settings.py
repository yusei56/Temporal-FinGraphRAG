"""Minimal runtime settings for TempoRAG-Fin."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


def _get_env_int(key: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _get_env_float(key: str, default: Optional[float]) -> Optional[float]:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a float, got {raw!r}") from exc


def _get_env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


NEO4J_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "neo4j://localhost:7687"),
    "username": os.getenv("NEO4J_USERNAME", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", ""),
    "max_pool_size": _get_env_int("NEO4J_MAX_POOL_SIZE", 10) or 10,
    "refresh_schema": _get_env_bool("NEO4J_REFRESH_SCHEMA", False),
}

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_EMBEDDINGS_MODEL = os.getenv("OPENAI_EMBEDDINGS_MODEL") or None
OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL") or None
LLM_TEMPERATURE = _get_env_float("TEMPERATURE", None)
LLM_MAX_TOKENS = _get_env_int("MAX_TOKENS", None)
LLM_TIMEOUT_SECONDS = _get_env_float("LLM_TIMEOUT_SECONDS", None)

OPENAI_EMBEDDING_CONFIG = {
    "model": OPENAI_EMBEDDINGS_MODEL,
    "api_key": OPENAI_API_KEY,
    "base_url": OPENAI_BASE_URL,
}

OPENAI_LLM_CONFIG = {
    "model": OPENAI_LLM_MODEL,
    "temperature": LLM_TEMPERATURE,
    "max_tokens": LLM_MAX_TOKENS,
    "timeout": LLM_TIMEOUT_SECONDS,
    "api_key": OPENAI_API_KEY,
    "base_url": OPENAI_BASE_URL,
}
