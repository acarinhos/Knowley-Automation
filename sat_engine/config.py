"""Environment loading and constants. Never prints secret values."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

FIRESTORE_COLLECTION = "generated_sat_questions"
SESSION_SIZE = {"rw": 54, "math": 44}
MODULE_SIZE = {"rw": 27, "math": 22}  # two modules per section


def find_env_file() -> Path | None:
    """SAT_ENV_PATH override, else nearest .env walking up from this file."""
    override = os.environ.get("SAT_ENV_PATH")
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    for d in Path(__file__).resolve().parents:
        if (d / ".env").is_file():
            return d / ".env"
    return None


def load_env() -> Path | None:
    path = find_env_file()
    if path:
        load_dotenv(path, override=False)
    return path


ENV_PATH = load_env()
ENV_DIR = ENV_PATH.parent if ENV_PATH else Path.cwd()


def get_env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
