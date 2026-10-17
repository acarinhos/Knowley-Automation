"""Which LLM providers may be used (UI selection), applied through the existing LLM_ALLOWED_PROVIDERS mechanism.

`MultiLLMManager` already filters its provider chain by the LLM_ALLOWED_PROVIDERS environment variable when it
is constructed. This module only decides the list (default gemini + groq), persists the user's choice in a small
git-ignored JSON file and applies it in-process (env var + fresh global manager) before a new job starts.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT_DIR / "ui_llm_providers.json"  # *.json is git-ignored
PROVIDERS = ("gemini", "groq", "cohere", "nvidia")
DEFAULT_SELECTION = ("gemini", "groq")
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cohere": "COHERE_API_KEY", "nvidia": "NVIDIA_API_KEY"}


def validate_selection(selection: Iterable[str]) -> List[str]:
    """Known providers in canonical order, no duplicates. ValueError when empty or unknown names are present."""
    chosen = {str(item).strip().casefold() for item in selection if str(item).strip()}
    unknown = chosen - set(PROVIDERS)
    if unknown:
        raise ValueError(f"Bilinmeyen LLM sağlayıcısı: {', '.join(sorted(unknown))}")
    if not chosen:
        raise ValueError("En az bir LLM sağlayıcısı seçilmelidir.")
    return [name for name in PROVIDERS if name in chosen]


def load_selection(path: Path = SETTINGS_FILE) -> List[str]:
    """Saved selection, or the default (gemini + groq) when the file is missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_selection(data.get("providers", []))
    except (OSError, ValueError, AttributeError):
        return list(DEFAULT_SELECTION)


def save_selection(selection: Iterable[str], path: Path = SETTINGS_FILE) -> List[str]:
    chosen = validate_selection(selection)
    # atomic: write a temp file in the same folder, then replace (a crash never leaves a half-written file)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(json.dumps({"providers": chosen}, indent=2) + "\n")
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return chosen


def allowed_env_value(selection: Iterable[str]) -> str:
    return ",".join(validate_selection(selection))


def key_status(environ: Optional[Dict[str, str]] = None) -> Dict[str, bool]:
    """Provider -> API key defined (value never exposed)."""
    env = os.environ if environ is None else environ
    return {name: bool(str(env.get(var, "")).strip()) for name, var in KEY_ENV.items()}


def apply_selection(selection: Iterable[str]) -> List[str]:
    """Use `selection` for every new LLM manager in this process (env var + reset of the shared manager)."""
    chosen = validate_selection(selection)
    from core import llm_manager  # local import: keeps the pure helpers importable without provider SDKs

    with llm_manager._global_llm_manager_lock:  # env write and manager reset are one step for get_llm_manager()
        os.environ["LLM_ALLOWED_PROVIDERS"] = ",".join(chosen)
        llm_manager._global_llm_manager = None
    return chosen
