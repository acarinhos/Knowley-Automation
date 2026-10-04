"""Global character catalogue contract shared by generation and Firestore import."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT_DIR / "data" / "global_character_catalog.json"
CHARACTER_SCHEMA_VERSION = "1.0.0"
CHARACTER_LOCALES = (
    "en", "de-DE", "ar", "bn-BD", "da-DK", "id", "fa-IR", "nl-NL", "fil",
    "fi-FI", "fr-FR", "hi-IN", "ja-JP", "ko-KR", "pl-PL", "hu-HU", "ms-MY",
    "no-NO", "pt-BR", "ro", "ru-RU", "th", "tr-TR", "uk", "ur", "vi",
    "el-GR", "cs-CZ", "zh-CN", "zh-TW", "iw-IL", "es-419", "es-ES", "sv-SE", "it-IT",
)


class CharacterEffect(BaseModel):
    """One ordered, machine-facing effect. Values and identifiers stay English."""

    model_config = ConfigDict(extra="allow")
    type: str
    trigger: str = "on_activate"
    params: dict[str, Any] = Field(default_factory=dict)
    conditions: dict[str, Any] = Field(default_factory=dict)


class CharacterSkill(BaseModel):
    model_config = ConfigDict(extra="allow")
    skill_slot: int = Field(ge=1, le=3)
    skill_id: str
    name: str
    source_description: str = ""
    action_type: str
    action_value: Optional[float] = None
    duration_seconds: Optional[int] = None
    daily_limits: dict[str, int]
    cooldown_questions: int = 0
    effects: list[CharacterEffect] = Field(default_factory=list)
    implementation_status: str = "catalog_only"


class CharacterDocument(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: str = CHARACTER_SCHEMA_VERSION
    id: str
    name: str
    title: str = ""
    role: str = ""
    category_id: str
    category_name: str
    sub_category_id: str
    sub_category_name: str
    region_code: str = "GL"
    character_type: str = "global"
    rarity: str = "unassigned"
    avatar_url: Optional[str] = None
    unlock_threshold: Optional[int] = None
    is_active: bool = False
    translations: dict[str, dict[str, str]]
    skills: list[CharacterSkill]

    @field_validator("region_code")
    @classmethod
    def global_only(cls, value: str) -> str:
        if value != "GL":
            raise ValueError("This catalogue contains global characters only (GL).")
        return value

    @field_validator("character_type")
    @classmethod
    def global_character_only(cls, value: str) -> str:
        if value != "global":
            raise ValueError("This catalogue contains global characters only.")
        return value

    @field_validator("skills")
    @classmethod
    def exactly_three_slots(cls, value: list[CharacterSkill]) -> list[CharacterSkill]:
        slots = sorted(skill.skill_slot for skill in value)
        if slots != [1, 2, 3]:
            raise ValueError("Each character must define skill slots 1, 2, and 3 exactly once.")
        return value


def slug_id(value: str) -> str:
    """Create a stable ASCII id from a taxonomy label."""
    normalized = unicodedata.normalize("NFKD", str(value or "").replace("ı", "i"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")


def load_global_character_catalog(
    path: str | Path = DEFAULT_CATALOG_PATH,
) -> list[CharacterDocument]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("characters") if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        raise ValueError("Global character catalogue must contain a characters array.")
    characters = [CharacterDocument.model_validate(row) for row in rows]
    ids = [character.id for character in characters]
    if len(ids) != len(set(ids)):
        raise ValueError("Character ids must be unique.")
    for character in characters:
        missing = set(CHARACTER_LOCALES) - set(character.translations)
        if missing:
            raise ValueError(f"{character.id} is missing locales: {', '.join(sorted(missing))}")
    return characters


def get_global_characters(
    category_id: Optional[str] = None,
    sub_category_id: Optional[str] = None,
    *,
    active_only: bool = True,
    path: str | Path = DEFAULT_CATALOG_PATH,
) -> list[CharacterDocument]:
    """Return global characters for a game category or subcategory."""
    return [
        character
        for character in load_global_character_catalog(path)
        if (not active_only or character.is_active)
        and (category_id is None or character.category_id == category_id)
        and (sub_category_id is None or character.sub_category_id == sub_category_id)
    ]


def user_character_progression_template() -> dict[str, Any]:
    """Safe initial state; server code must own unlocks, resets, and equipment."""
    return {
        "is_unlocked": False,
        "current_level": 1,
        "experience_points": 0,
        "daily_uses": {"slot_1": 0, "slot_2": 0, "slot_3": 0},
        "last_reset_date": "",
        "is_equipped": False,
        "schema_version": CHARACTER_SCHEMA_VERSION,
    }


def firestore_character_document(character: CharacterDocument) -> dict[str, Any]:
    """Return a clean document payload without server-managed timestamps."""
    return character.model_dump(mode="python")


def supported_action_types(characters: Iterable[CharacterDocument]) -> set[str]:
    return {
        effect.type
        for character in characters
        for skill in character.skills
        for effect in skill.effects
    }
