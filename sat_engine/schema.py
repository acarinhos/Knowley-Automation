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
Difficulty = Literal["easy", "medium", "hard"]
_GRID_RE = re.compile(r"^-?(\d+(\.\d+)?|\.\d+|\d+/\d+)$")


class QuestionMeta(BaseModel):
    blueprint_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    originality_score: Optional[float] = Field(default=None, ge=0, le=1)


class SATQuestion(BaseModel):
    id: str
    section: Section
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

    @model_validator(mode="after")
    def _check(self):
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
            if self.options:
                raise ValueError("grid_in must not have options")
            if not _GRID_RE.match(self.correct_answer.strip()):
                raise ValueError("grid_in answer must be numeric or a fraction")
        return self
