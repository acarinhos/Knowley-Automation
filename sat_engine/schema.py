"""Pydantic v2 schema + official digital SAT taxonomy."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

TAXONOMY: dict[str, dict[str, list[str]]] = {
    "rw": {
        "Craft and Structure": ["Cross-Text Connections", "Text Structure and Purpose", "Words in Context"],
        "Information and Ideas": ["Central Ideas and Details", "Command of Evidence", "Inferences"],
        "Standard English Conventions": ["Boundaries", "Form, Structure, and Sense"],
        "Expression of Ideas": ["Rhetorical Synthesis", "Transitions"],
    },
    "math": {
        "Algebra": [
            "Linear equations in one variable", "Linear functions", "Linear equations in two variables",
            "Systems of two linear equations in two variables", "Linear inequalities in one or two variables",
        ],
        "Advanced Math": [
            "Equivalent expressions", "Nonlinear equations in one variable and systems of equations in two variables",
            "Nonlinear functions",
        ],
        "Problem-Solving and Data Analysis": [
            "Ratios, rates, proportional relationships, and units", "Percentages",
            "One-variable data: distributions and measures of center and spread", "Two-variable data: models and scatterplots",
            "Probability and conditional probability", "Inference from sample statistics and margin of error",
            "Evaluating statistical claims: observational studies and experiments",
        ],
        "Geometry and Trigonometry": [
            "Area and volume", "Lines, angles, and triangles", "Right triangles and trigonometry", "Circles",
        ],
    },
}

Section = Literal["rw", "math"]
TUR = {"rw": "eng", "math": "math"}
Difficulty = Literal["easy", "medium", "hard"]
_GRID_RE = re.compile(r"^-?(\d+(\.\d+)?|\.\d+|\d+/\d+)$")


_NULLS = {"", "null", "none", "n/a", "na", "nil"}
_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))
_SPACES = dict.fromkeys(map(ord, "\u00a0\u202f\u2007\u2009\u200a\u2002\u2003"), " ")
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"'})


def normalize_text(s, math: bool = False):
    """Single typography normaliser for all generated text (non-str passes through)."""
    if not isinstance(s, str):
        return s
    s = s.translate(_ZW).translate(_SPACES).translate(_QUOTES)
    s = re.sub(r"(?<=\w)[\u2010\u2011\u2013](?=\w)", "-", s)
    s = s.replace("\u2010", "-").replace("\u2011", "-")
    return s if math else s.replace("\u2212", "-")


def _none_if_null(v):
    return None if v is None or (isinstance(v, str) and v.strip().lower() in _NULLS) or v == {} else v


class QuestionMeta(BaseModel):
    blueprint_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    originality_score: Optional[float] = Field(default=None, ge=0, le=1)


class SATQuestion(BaseModel):
    id: str
    section: Section
    tur: Literal["eng", "math"]  # derived from section (rw -> eng, math -> math); never stored independently
    module: Literal[1, 2]
    difficulty: Difficulty
    domain: str
    skill: str
    passage: Optional[str] = None
    stem: str = Field(min_length=1)
    answer_type: Literal["mcq", "grid_in"]
    options: Optional[dict[str, str]] = None
    correct_answer: str
    explanation: str
    visual: Optional[dict] = None  # validated later by the visuals package
    meta: QuestionMeta = Field(default_factory=QuestionMeta)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, d):
        if not isinstance(d, dict):
            return d
        d = dict(d)
        math = d.get("section") == "math"
        if d.get("tur") in (None, "") and d.get("section") in TUR:
            d["tur"] = TUR[d["section"]]
        for k in ("passage", "options"):
            d[k] = _none_if_null(d.get(k))
        for k in ("stem", "passage", "explanation", "correct_answer"):
            d[k] = normalize_text(d.get(k), math) if k in d else None
            if d[k] is None:
                d.pop(k)
        if isinstance(d.get("options"), dict):
            d["options"] = {k: normalize_text(v, math) for k, v in d["options"].items()}
        if d.get("answer_type") == "grid_in":
            d["options"] = None
        return d

    @model_validator(mode="after")
    def _check(self):
        if self.tur != TUR[self.section]:
            raise ValueError(f"tur {self.tur!r} inconsistent with section {self.section!r}")
        if self.domain not in TAXONOMY[self.section]:
            raise ValueError(f"unknown domain {self.domain!r} for section {self.section}")
        if self.skill not in TAXONOMY[self.section][self.domain]:
            raise ValueError(f"unknown skill {self.skill!r} for domain {self.domain}")
        if self.answer_type == "mcq":
            o = self.options or {}
            if sorted(o) != ["A", "B", "C", "D"]:
                raise ValueError("mcq needs exactly options A-D")
            if len({v.strip().lower() for v in o.values()}) != 4 or any(not v.strip() for v in o.values()):
                raise ValueError("mcq options must be 4 distinct non-empty strings")
            if self.correct_answer not in o:
                raise ValueError("correct_answer must be one of A-D")
        else:
            if re.search(r"wrong (choice|option|answer)s?|incorrect (choice|option)s?|distractor", self.explanation, re.I):
                raise ValueError("grid_in explanation must not discuss answer choices")
            if not _GRID_RE.match(self.correct_answer.strip()):
                raise ValueError("grid_in answer must be numeric or a fraction")
        return self
