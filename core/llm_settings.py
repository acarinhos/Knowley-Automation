"""Which LLM providers and models may be used (UI selection), applied through the existing env mechanisms.

`MultiLLMManager` already filters its provider chain by the LLM_ALLOWED_PROVIDERS environment variable when it
is constructed. This module only decides the list (default gemini + groq), persists the user's choice in a small
git-ignored JSON file and applies it in-process (env var + fresh global manager) before a new job starts.
Models use the manager's own GEMINI_MODELS / GROQ_MODELS / COHERE_MODELS / NVIDIA_MODELS lists (same order,
filtered to the selected ones); a provider without a stored model choice uses all of its models.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT_DIR / "ui_llm_providers.json"  # *.json is git-ignored
PROVIDERS = ("gemini", "groq", "cohere", "nvidia", "claude")  # claude = optional CLI, never in the default
DEFAULT_SELECTION = ("gemini", "groq")
KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cohere": "COHERE_API_KEY", "nvidia": "NVIDIA_API_KEY"}  # claude has no key: see key_status


MODEL_ENV = {"gemini": "GEMINI_MODELS", "groq": "GROQ_MODELS", "cohere": "COHERE_MODELS", "nvidia": "NVIDIA_MODELS", "claude": "CLAUDE_MODELS"}


def _original_models() -> Dict[str, List[str]]:
    """Model catalogue per provider: the .env override if present, else the manager's defaults (captured once)."""
    from core.llm_manager import MultiLLMManager

    defaults = {"gemini": MultiLLMManager.GEMINI_MODELS, "groq": MultiLLMManager.GROQ_MODELS,
                "cohere": MultiLLMManager.COHERE_MODELS, "nvidia": MultiLLMManager.NVIDIA_MODELS,
                "claude": MultiLLMManager.CLAUDE_MODELS}
    result = {}
    for name, fallback in defaults.items():
        raw = os.environ.get(MODEL_ENV[name], "")
        values = [item.strip() for item in raw.split(",") if item.strip()]
        result[name] = values or list(fallback)
    return result


_CATALOGUE: Optional[Dict[str, List[str]]] = None


def available_models(provider: str) -> List[str]:
    """All models of a provider, in the manager's order. Captured before any selection changes the env."""
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = _original_models()
    return list(_CATALOGUE[provider])


def validate_models(models: Optional[Dict[str, Iterable[str]]], providers: Iterable[str]) -> Dict[str, List[str]]:
    """Chosen models per ENABLED provider (catalogue order). Missing entry = all models; empty/unknown = ValueError."""
    models = models or {}
    result: Dict[str, List[str]] = {}
    for provider in providers:
        catalogue = available_models(provider)
        if provider not in models:
            result[provider] = catalogue
            continue
        chosen = {str(item).strip() for item in models[provider] if str(item).strip()}
        unknown = chosen - set(catalogue)
        if unknown:
            raise ValueError(f"{provider}: bilinmeyen model {', '.join(sorted(unknown))}")
        if not chosen:
            raise ValueError(f"{provider} için en az bir model seçilmelidir.")
        result[provider] = [name for name in catalogue if name in chosen]
    return result


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


def load_models(path: Path = SETTINGS_FILE) -> Dict[str, List[str]]:
    """Saved model choice per enabled provider (all models when nothing valid is stored)."""
    providers = load_selection(path)
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("models") or {}
        return validate_models({k: v for k, v in stored.items() if k in providers}, providers)
    except (OSError, ValueError, AttributeError, TypeError):
        return validate_models(None, providers)


def save_selection(selection: Iterable[str], path: Path = SETTINGS_FILE,
                   models: Optional[Dict[str, Iterable[str]]] = None) -> List[str]:
    chosen = validate_selection(selection)
    chosen_models = validate_models(models, chosen)
    try:
        extra = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k == "translation_use_azure"}
    except (OSError, ValueError, AttributeError):
        extra = {}
    # atomic: write a temp file in the same folder, then replace (a crash never leaves a half-written file)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(json.dumps({"providers": chosen, "models": chosen_models, **extra}, indent=2) + "\n")
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
    status = {name: bool(str(env.get(var, "")).strip()) for name, var in KEY_ENV.items()}
    if environ is None:  # claude "key" = CLI installed and logged in (subprocess check, so only for the live environment)
        from core.llm_manager import claude_ready

        status["claude"] = claude_ready()
    return status


def model_env(selection: Iterable[str], models: Optional[Dict[str, Iterable[str]]] = None) -> Dict[str, str]:
    """Environment values for a selection: LLM_ALLOWED_PROVIDERS plus <PROVIDER>_MODELS for each enabled provider."""
    chosen = validate_selection(selection)
    values = {"LLM_ALLOWED_PROVIDERS": ",".join(chosen)}
    for provider, names in validate_models(models, chosen).items():
        values[MODEL_ENV[provider]] = ",".join(names)
    return values


def apply_selection(selection: Iterable[str], models: Optional[Dict[str, Iterable[str]]] = None) -> List[str]:
    """Use `selection` (and model choice) for every new LLM manager in this process (env + shared-manager reset)."""
    chosen = validate_selection(selection)
    values = model_env(chosen, models)
    from core import llm_manager  # local import: keeps the pure helpers importable without provider SDKs

    with llm_manager._global_llm_manager_lock:  # env write and manager reset are one step for get_llm_manager()
        os.environ.update(values)
        llm_manager._global_llm_manager = None
    return chosen


# --- translation mode (Azure on/off), persisted in the same JSON and applied through TRANSLATION_USE_AZURE ---
def _env_flag(default: str = "1") -> bool:
    return os.environ.get("TRANSLATION_USE_AZURE", default).strip().casefold() not in {"0", "false", "no", "off"}


def load_use_azure(path: Path = SETTINGS_FILE) -> bool:
    """An explicitly set (non-empty) TRANSLATION_USE_AZURE env wins for this process, else the persisted choice, else on."""
    if os.environ.get("TRANSLATION_USE_AZURE", "").strip():
        return _env_flag()
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("translation_use_azure")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if isinstance(value, bool) else _env_flag()


def apply_translation_mode(use_azure: bool, path: Path = SETTINGS_FILE) -> bool:
    """Set TRANSLATION_USE_AZURE for this process (read at call time by the translator) and persist the choice."""
    use_azure = bool(use_azure)
    os.environ["TRANSLATION_USE_AZURE"] = "1" if use_azure else "0"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["translation_use_azure"] = use_azure
    try:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False)
        with handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.replace(handle.name, path)
    except OSError:
        pass  # persistence is best effort; the env value already applies to this process
    return use_azure
